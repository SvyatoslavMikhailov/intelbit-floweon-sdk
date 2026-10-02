"""PluginRunner — запуск плагина в отдельном subprocess (ADR-006 v1.1 §Sandbox embedded).

Плагин-разработчик не работает с этим напрямую — он реализует ConnectorPlugin/
TransformerPlugin/NotifierPlugin, а PluginRunner запускает его в child-process.

v0.3.0:
- в дочернем процессе — один долгоживущий event loop (поток), а не asyncio.run
  на каждый вызов: состояние клиента плагина (rate limiter, сессии) живёт между
  вызовами;
- конкурентные вызовы с correlation id: семафор `max_concurrency` и отдельная
  «быстрая полоса» (`fast_methods`, по умолчанию subscribe/health) — приём
  вебхука не ждёт долгую запись каталога;
- circuit breaker с half-open: после `cooldown_sec` пропускается пробный вызов,
  успех закрывает breaker.
"""

import asyncio
import importlib
import json
import multiprocessing
import queue
import threading
import time
import uuid
from typing import Any

from pydantic import BaseModel


class PluginRunnerConfig(BaseModel):
    """Конфигурация изолированного запуска плагина."""

    timeout_sec: int = 30
    memory_mb: int = 512
    circuit_breaker_failures: int = 5
    circuit_breaker_window_sec: int = 300
    # Через сколько секунд открытый breaker пропускает пробный вызов (half-open).
    cooldown_sec: float = 30.0
    # Одновременных вызовов обычной полосы в дочернем процессе.
    max_concurrency: int = 4
    # Методы быстрой полосы — без очереди за обычными вызовами.
    fast_methods: tuple[str, ...] = ("subscribe", "health", "health_check")


class PluginTimeoutError(RuntimeError):
    """Плагин не ответил в течение timeout_sec секунд."""


class PluginDisabledError(RuntimeError):
    """Circuit breaker открыт — плагин временно отключён."""


class PluginCallError(RuntimeError):
    """Плагин вернул ошибку."""


class PluginRestartedError(PluginCallError):
    """Процесс плагина перезапущен (таймаут чужого вызова или падение) во время вызова.

    Результат вызова неизвестен: запрос мог быть исполнен частично. Вызывающая
    сторона решает, безопасен ли повтор (чтение — да, запись — нет). В circuit
    breaker не засчитывается — сбой уже учтён один раз.
    """


class CircuitBreaker:
    """Окно ошибок с отключением плагина и half-open восстановлением.

    closed → (max_failures в окне) → open → (cooldown_sec) → half_open: один
    пробный вызов; успех → closed, ошибка → снова open.
    """

    def __init__(self, max_failures: int, window_sec: int, cooldown_sec: float = 30.0) -> None:
        self._max_failures = max_failures
        self._window_sec = window_sec
        self._cooldown_sec = cooldown_sec
        self._failures: list[float] = []
        self._disabled = False
        self._opened_at = 0.0
        self._probing = False

    def record_failure(self) -> None:
        now = time.monotonic()
        if self._disabled or self._probing:
            # Пробный вызов не удался — снова open на полный cooldown.
            self._disabled = True
            self._probing = False
            self._opened_at = now
            return
        self._failures = [t for t in self._failures if now - t < self._window_sec]
        self._failures.append(now)
        if len(self._failures) >= self._max_failures:
            self._disabled = True
            self._opened_at = now

    def abort_probe(self) -> None:
        """Пробный вызов не дал результата (отмена, ошибка до отправки) — снять флаг пробы.

        Breaker остаётся открытым; следующий вызов после cooldown снова станет пробой.
        Без этого прерванная проба навсегда оставляла бы breaker в half-open.
        """
        self._probing = False

    @property
    def probing(self) -> bool:
        return self._probing

    def record_success(self) -> None:
        if self._probing or self._disabled:
            self.reset()

    def is_open(self) -> bool:
        """True — вызов запрещён. После cooldown один вызов пропускается (half-open)."""
        if not self._disabled:
            return False
        if self._probing:
            return True  # пробный вызов уже в полёте
        if time.monotonic() - self._opened_at >= self._cooldown_sec:
            self._probing = True
            return False
        return True

    @property
    def state(self) -> str:
        if not self._disabled:
            return "closed"
        if self._probing or time.monotonic() - self._opened_at >= self._cooldown_sec:
            return "half_open"
        return "open"

    def reset(self) -> None:
        self._failures.clear()
        self._disabled = False
        self._probing = False
        self._opened_at = 0.0

    @property
    def failures_count(self) -> int:
        now = time.monotonic()
        return len([t for t in self._failures if now - t < self._window_sec])


# --------------------------------------------------------------------------- #
# Дочерний процесс
# --------------------------------------------------------------------------- #


def _plugin_worker(
    class_path: str,
    init_kwargs: dict[str, Any],
    request_queue: "multiprocessing.Queue[str]",
    response_queue: "multiprocessing.Queue[str]",
    memory_mb: int,
    max_concurrency: int = 4,
    fast_methods: tuple[str, ...] = (),
) -> None:
    """Worker дочернего процесса.

    IPC: JSON-строки через multiprocessing.Queue (pickle запрещён на уровне
    приложения). Запрос {id, method, payload} → ответ {id, result|error}.
    Плагин живёт в одном event loop (отдельный поток) весь срок процесса.
    """
    try:
        import resource as _resource

        memory_bytes = memory_mb * 1024 * 1024
        _resource.setrlimit(_resource.RLIMIT_AS, (memory_bytes, _resource.RLIM_INFINITY))
    except Exception:
        pass

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    async def build() -> Any:
        module_path, class_name = class_path.rsplit(".", 1)
        plugin_cls = getattr(importlib.import_module(module_path), class_name)
        return plugin_cls(**init_kwargs)

    try:
        # Плагин создаётся внутри loop: его asyncio-примитивы привязаны к нему.
        plugin = asyncio.run_coroutine_threadsafe(build(), loop).result()
    except Exception as e:
        response_queue.put(json.dumps({"id": None, "error": f"Failed to load plugin: {e}"}))
        return

    async def make_semaphores() -> tuple[asyncio.Semaphore, asyncio.Semaphore]:
        size = max(1, max_concurrency)
        return asyncio.Semaphore(size), asyncio.Semaphore(size)

    normal_lane, fast_lane = asyncio.run_coroutine_threadsafe(make_semaphores(), loop).result()

    async def handle(request_id: str, method_name: str, payload: dict[str, Any]) -> None:
        lane = fast_lane if method_name in fast_methods else normal_lane
        try:
            async with lane:
                method = getattr(plugin, method_name)
                if asyncio.iscoroutinefunction(method):
                    result: Any = await method(payload)
                else:
                    result = await asyncio.get_running_loop().run_in_executor(None, method, payload)
            json.dumps(result)  # проверяем JSON-сериализуемость
            message = {"id": request_id, "result": result if result is not None else {}}
        except Exception as e:
            message = {"id": request_id, "error": str(e)}
        response_queue.put(json.dumps(message))

    while True:
        try:
            raw = request_queue.get()
            request = json.loads(raw)
        except Exception as e:
            response_queue.put(json.dumps({"id": None, "error": f"IPC parse error: {e}"}))
            continue

        method_name: str = request.get("method", "")
        if method_name == "__poison__":
            break
        asyncio.run_coroutine_threadsafe(
            handle(str(request.get("id")), method_name, request.get("payload", {})), loop
        )

    loop.call_soon_threadsafe(loop.stop)


# --------------------------------------------------------------------------- #
# Родительская сторона
# --------------------------------------------------------------------------- #


class _Generation:
    """Один запущенный дочерний процесс: очереди, поток чтения ответов, ожидающие вызовы."""

    def __init__(
        self,
        process: multiprocessing.Process,
        requests: Any,
        responses: Any,
        on_death: Any = None,
    ) -> None:
        self.on_death = on_death
        self.process = process
        self.requests = requests
        self.responses = responses
        self.pending: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Future[Any]]] = {}
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def register(self, request_id: str) -> asyncio.Future[Any]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        with self.lock:
            self.pending[request_id] = (loop, future)
        return future

    def forget(self, request_id: str) -> None:
        with self.lock:
            self.pending.pop(request_id, None)

    def _deliver(self, request_id: str | None, message: dict[str, Any]) -> None:
        with self.lock:
            if request_id is None:
                # Ошибка загрузки плагина / IPC — относится ко всем ожидающим.
                targets = list(self.pending.values())
                self.pending.clear()
            else:
                entry = self.pending.pop(request_id, None)
                targets = [entry] if entry else []
        for loop, future in targets:
            loop.call_soon_threadsafe(_resolve, future, message)

    def _read(self) -> None:
        while not self.stopped.is_set():
            try:
                raw = self.responses.get(True, 0.2)
            except queue.Empty:
                if not self.process.is_alive():
                    # Процесс умер без ответа (краш, OOM) — ожидающие не должны висеть.
                    # Один сбой на падение процесса, а не по сбою на каждый вызов в полёте.
                    if self.on_death is not None and self.pending:
                        self.on_death()
                    self.fail_all("plugin process died")
                    return
                continue
            except (EOFError, OSError):
                return
            message = json.loads(raw)
            self._deliver(message.get("id"), message)

    def fail_all(self, reason: str) -> None:
        with self.lock:
            targets = list(self.pending.values())
            self.pending.clear()
        for loop, future in targets:
            loop.call_soon_threadsafe(_resolve, future, {"error": reason, "restarted": True})

    def stop(self) -> None:
        self.stopped.set()


def _resolve(future: asyncio.Future[Any], message: dict[str, Any]) -> None:
    if not future.done():
        future.set_result(message)


class PluginRunner:
    """Запускает плагин в отдельном subprocess с timeout, memory limit и circuit breaker."""

    def __init__(self, config: PluginRunnerConfig | None = None) -> None:
        self._config = config or PluginRunnerConfig()
        self._process: multiprocessing.Process | None = None
        self._request_queue: multiprocessing.Queue[str] | None = None
        self._response_queue: multiprocessing.Queue[str] | None = None
        self._generation: _Generation | None = None
        self._circuit_breaker = CircuitBreaker(
            self._config.circuit_breaker_failures,
            self._config.circuit_breaker_window_sec,
            self._config.cooldown_sec,
        )
        self._last_error: str | None = None
        # Параметры запуска — для ленивого respawn после timeout/краша.
        self._class_path: str | None = None
        self._init_kwargs: dict[str, Any] = {}

    def start(self, plugin_class_path: str, init_kwargs: dict[str, Any] | None = None) -> None:
        """Запустить дочерний процесс с плагином.

        Args:
            plugin_class_path: FQN класса плагина (например, 'my_pkg.MyConnector').
            init_kwargs: Аргументы для конструктора плагина.
        """
        self._class_path = plugin_class_path
        self._init_kwargs = init_kwargs or {}
        ctx = multiprocessing.get_context("spawn")
        self._request_queue = ctx.Queue()
        self._response_queue = ctx.Queue()
        proc: multiprocessing.Process = ctx.Process(  # type: ignore[assignment]
            target=_plugin_worker,
            args=(
                plugin_class_path,
                init_kwargs or {},
                self._request_queue,
                self._response_queue,
                self._config.memory_mb,
                self._config.max_concurrency,
                tuple(self._config.fast_methods),
            ),
            daemon=True,
        )
        self._process = proc
        proc.start()
        self._generation = _Generation(
            proc, self._request_queue, self._response_queue, self._circuit_breaker.record_failure
        )

    async def call(self, method_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Вызвать метод плагина через IPC (вызовы могут идти параллельно).

        Payload должен быть JSON-сериализуемым (не pickle-объекты).

        Raises:
            PluginDisabledError: circuit breaker открыт.
            RuntimeError: runner не запущен.
            TypeError: payload не JSON-сериализуем.
            PluginTimeoutError: плагин не ответил за timeout_sec.
            PluginCallError: плагин вернул ошибку.
        """
        request_id = uuid.uuid4().hex
        # JSON-валидация payload до breaker'а — предотвращает передачу pickle-объектов
        # и не тратит пробный вызов half-open на заведомо невалидный payload.
        request_json = json.dumps({"id": request_id, "method": method_name, "payload": payload})

        if self._circuit_breaker.is_open():
            raise PluginDisabledError("Plugin circuit breaker is open")
        probe = self._circuit_breaker.probing
        try:
            return await self._call(method_name, request_id, request_json)
        finally:
            # Проба, завершившаяся без record_success/record_failure (отмена задачи,
            # сбой запуска процесса), не должна оставить breaker в half-open навсегда.
            if probe and self._circuit_breaker.probing:
                self._circuit_breaker.abort_probe()

    async def _call(self, method_name: str, request_id: str, request_json: str) -> dict[str, Any]:
        self._ensure_running()
        generation = self._generation
        assert generation is not None and self._request_queue is not None
        future = generation.register(request_id)
        self._request_queue.put(request_json)

        try:
            message = await asyncio.wait_for(future, timeout=self._config.timeout_sec)
        except TimeoutError:
            generation.forget(request_id)
            # Зависший вызов держит слот — процесс перезапускается; остальным
            # вызовам этого процесса — PluginRestartedError, respawn ленивый.
            self._kill("plugin restarted after timeout")
            self._circuit_breaker.record_failure()
            self._last_error = f"timeout in {method_name}"
            raise PluginTimeoutError(
                f"Plugin method {method_name!r} timed out after {self._config.timeout_sec}s"
            ) from None

        if message.get("restarted"):
            raise PluginRestartedError(message["error"])
        if "error" in message:
            self._circuit_breaker.record_failure()
            self._last_error = str(message["error"])[:500]
            raise PluginCallError(message["error"])

        self._circuit_breaker.record_success()
        return message.get("result", {})  # type: ignore[no-any-return]

    def _kill(self, reason: str) -> None:
        if self._generation is not None:
            self._generation.stop()
            self._generation.fail_all(reason)
        if self._process is not None:
            self._process.terminate()
        self._process = None
        self._request_queue = None
        self._response_queue = None
        self._generation = None

    def _ensure_running(self) -> None:
        """Гарантировать живой subprocess: respawn после timeout/краша.

        Raises:
            RuntimeError: плагин ни разу не запускался (start() не вызывался).
        """
        if self._process is not None and self._process.is_alive():
            return
        if self._class_path is None:
            raise RuntimeError("PluginRunner is not started; call start() first")
        if self._generation is not None:
            self._generation.stop()
        # Перезапуск после timeout/краша — иначе один timeout убивал плагин навсегда.
        self.start(self._class_path, self._init_kwargs)

    async def stop(self) -> None:
        """Graceful shutdown: poison pill → join(5s) → terminate."""
        if self._process is None:
            return
        try:
            if self._request_queue is not None:
                self._request_queue.put(json.dumps({"method": "__poison__", "payload": {}}))
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._process.join, 5)
            if self._process.is_alive():
                self._process.terminate()
        finally:
            if self._generation is not None:
                self._generation.stop()
                self._generation.fail_all("plugin stopped")
            self._process = None
            self._request_queue = None
            self._response_queue = None
            self._generation = None

    def health(self) -> dict[str, Any]:
        """Статус плагина для admin-UI и мониторинга."""
        alive = self._process is not None and self._process.is_alive()
        return {
            "alive": alive,
            "failures_count": self._circuit_breaker.failures_count,
            "disabled": self._circuit_breaker.state == "open",
            "breaker": self._circuit_breaker.state,
            "in_flight": len(self._generation.pending) if self._generation else 0,
            "last_error": self._last_error,
        }

    def reset_circuit_breaker(self) -> None:
        """Ручной re-enable плагина после circuit breaker."""
        self._circuit_breaker.reset()
