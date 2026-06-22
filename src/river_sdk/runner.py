"""PluginRunner — запуск плагина в отдельном subprocess (ADR-006 v1.1 §Sandbox embedded).

Плагин-разработчик не работает с этим напрямую — он реализует ConnectorPlugin/
TransformerPlugin/NotifierPlugin, а PluginRunner запускает его в child-process.
"""

import asyncio
import importlib
import json
import multiprocessing
import queue
import time
from typing import Any

from pydantic import BaseModel


class PluginRunnerConfig(BaseModel):
    """Конфигурация изолированного запуска плагина."""

    timeout_sec: int = 30
    memory_mb: int = 512
    circuit_breaker_failures: int = 5
    circuit_breaker_window_sec: int = 300


class PluginTimeoutError(RuntimeError):
    """Плагин не ответил в течение timeout_sec секунд."""


class PluginDisabledError(RuntimeError):
    """Circuit breaker открыт — плагин временно отключён."""


class PluginCallError(RuntimeError):
    """Плагин вернул ошибку."""


class CircuitBreaker:
    """Окно ошибок с автоматическим отключением плагина при превышении порога."""

    def __init__(self, max_failures: int, window_sec: int) -> None:
        self._max_failures = max_failures
        self._window_sec = window_sec
        self._failures: list[float] = []
        self._disabled = False

    def record_failure(self) -> None:
        now = time.monotonic()
        self._failures = [t for t in self._failures if now - t < self._window_sec]
        self._failures.append(now)
        if len(self._failures) >= self._max_failures:
            self._disabled = True

    def is_open(self) -> bool:
        return self._disabled

    def reset(self) -> None:
        self._failures.clear()
        self._disabled = False

    @property
    def failures_count(self) -> int:
        now = time.monotonic()
        return len([t for t in self._failures if now - t < self._window_sec])


def _plugin_worker(
    class_path: str,
    init_kwargs: dict[str, Any],
    request_queue: "multiprocessing.Queue[str]",
    response_queue: "multiprocessing.Queue[str]",
    memory_mb: int,
) -> None:
    """Worker, выполняющийся в дочернем процессе.

    IPC-протокол: JSON-строки через multiprocessing.Queue.
    pickle запрещён на уровне приложения — все данные сериализуются в JSON.
    """
    try:
        import resource as _resource

        memory_bytes = memory_mb * 1024 * 1024
        _resource.setrlimit(_resource.RLIMIT_AS, (memory_bytes, _resource.RLIM_INFINITY))
    except Exception:
        pass

    try:
        module_path, class_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        plugin_cls = getattr(module, class_name)
        plugin = plugin_cls(**init_kwargs)
    except Exception as e:
        response_queue.put(json.dumps({"error": f"Failed to load plugin: {e}"}))
        return

    while True:
        try:
            raw = request_queue.get()
            request = json.loads(raw)
        except Exception as e:
            response_queue.put(json.dumps({"error": f"IPC parse error: {e}"}))
            continue

        method_name: str = request.get("method", "")
        if method_name == "__poison__":
            break

        payload: dict[str, Any] = request.get("payload", {})
        try:
            method = getattr(plugin, method_name)
            if asyncio.iscoroutinefunction(method):
                result: Any = asyncio.run(method(payload))
            else:
                result = method(payload)
            json.dumps(result)  # проверяем JSON-сериализуемость
            response_queue.put(json.dumps({"result": result if result is not None else {}}))
        except Exception as e:
            response_queue.put(json.dumps({"error": str(e)}))


class PluginRunner:
    """Запускает плагин в отдельном subprocess с timeout, memory limit и circuit breaker."""

    def __init__(self, config: PluginRunnerConfig | None = None) -> None:
        self._config = config or PluginRunnerConfig()
        self._process: multiprocessing.Process | None = None
        self._request_queue: multiprocessing.Queue[str] | None = None
        self._response_queue: multiprocessing.Queue[str] | None = None
        self._circuit_breaker = CircuitBreaker(
            self._config.circuit_breaker_failures,
            self._config.circuit_breaker_window_sec,
        )
        # Параметры запуска — для ленивого respawn после timeout/краша.
        self._class_path: str | None = None
        self._init_kwargs: dict[str, Any] = {}
        # Сериализует call(): один request/response queue без correlation_id,
        # параллельные вызовы иначе перемешивают ответы между корутинами.
        self._call_lock = asyncio.Lock()

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
            ),
            daemon=True,
        )
        self._process = proc
        proc.start()

    async def call(self, method_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Вызвать метод плагина через IPC.

        Payload должен быть JSON-сериализуемым (не pickle-объекты).

        Raises:
            PluginDisabledError: circuit breaker открыт.
            RuntimeError: runner не запущен.
            TypeError: payload не JSON-сериализуем.
            PluginTimeoutError: плагин не ответил за timeout_sec.
            PluginCallError: плагин вернул ошибку.
        """
        if self._circuit_breaker.is_open():
            raise PluginDisabledError("Plugin circuit breaker is open")

        # JSON-валидация payload — предотвращает передачу произвольных pickle-объектов.
        # Вне lock: ошибка сериализации не должна занимать слот вызова.
        request_json = json.dumps({"method": method_name, "payload": payload})

        async with self._call_lock:
            self._ensure_running()
            assert self._request_queue is not None and self._response_queue is not None

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._request_queue.put, request_json)

            try:
                # get(timeout=...) с собственным таймаутом, НЕ asyncio.wait_for поверх
                # бесконечного get: иначе отмена future оставляла бы поток executor
                # вечно заблокированным на queue.get() → утечка потока и зависание
                # интерпретатора на teardown (поток не daemon).
                raw = await loop.run_in_executor(
                    None, self._response_queue.get, True, self._config.timeout_sec
                )
            except queue.Empty:
                # Процесс мог зависнуть на этом запросе — убиваем. Очереди обнуляем,
                # чтобы запоздавший ответ не попал в следующий вызов; respawn ленивый.
                if self._process is not None:
                    self._process.terminate()
                self._process = None
                self._request_queue = None
                self._response_queue = None
                self._circuit_breaker.record_failure()
                raise PluginTimeoutError(
                    f"Plugin method {method_name!r} timed out after {self._config.timeout_sec}s"
                ) from None

        result = json.loads(raw)
        if "error" in result:
            self._circuit_breaker.record_failure()
            raise PluginCallError(result["error"])

        return result.get("result", {})  # type: ignore[no-any-return]

    def _ensure_running(self) -> None:
        """Гарантировать живой subprocess: respawn после timeout/краша.

        Raises:
            RuntimeError: плагин ни разу не запускался (start() не вызывался).
        """
        if self._process is not None and self._process.is_alive():
            return
        if self._class_path is None:
            raise RuntimeError("PluginRunner is not started; call start() first")
        # Перезапуск после timeout/краша — иначе один timeout убивал плагин навсегда.
        self.start(self._class_path, self._init_kwargs)

    async def stop(self) -> None:
        """Graceful shutdown: poison pill → join(5s) → terminate."""
        if self._process is None:
            return
        try:
            if self._request_queue is not None:
                self._request_queue.put(json.dumps({"method": "__poison__", "payload": {}}))
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.terminate()
        finally:
            self._process = None
            self._request_queue = None
            self._response_queue = None

    def health(self) -> dict[str, Any]:
        """Статус плагина для admin-UI и мониторинга."""
        alive = self._process is not None and self._process.is_alive()
        return {
            "alive": alive,
            "failures_count": self._circuit_breaker.failures_count,
            "disabled": self._circuit_breaker.is_open(),
            "last_error": None,
        }

    def reset_circuit_breaker(self) -> None:
        """Ручной re-enable плагина после circuit breaker."""
        self._circuit_breaker.reset()
