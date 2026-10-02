"""Тесты PluginRunner и CircuitBreaker."""

import time
from unittest.mock import MagicMock

import pytest

from floweon_sdk.runner import (
    CircuitBreaker,
    PluginCallError,
    PluginDisabledError,
    PluginRunner,
    PluginRunnerConfig,
    PluginTimeoutError,
)

# --------------------------------------------------------------------------- #
# CircuitBreaker
# --------------------------------------------------------------------------- #


class TestCircuitBreaker:
    def test_initial_not_open(self) -> None:
        cb = CircuitBreaker(max_failures=5, window_sec=300)
        assert not cb.is_open()
        assert cb.failures_count == 0

    def test_record_failure_below_threshold(self) -> None:
        cb = CircuitBreaker(max_failures=5, window_sec=300)
        for _ in range(4):
            cb.record_failure()
        assert not cb.is_open()
        assert cb.failures_count == 4

    def test_opens_on_max_failures(self) -> None:
        cb = CircuitBreaker(max_failures=3, window_sec=300)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open()

    def test_reset_clears_state(self) -> None:
        cb = CircuitBreaker(max_failures=3, window_sec=300)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open()
        cb.reset()
        assert not cb.is_open()
        assert cb.failures_count == 0

    def test_failures_count_excludes_expired(self) -> None:
        cb = CircuitBreaker(max_failures=5, window_sec=1)
        cb.record_failure()
        cb.record_failure()
        # Сдвигаем время вперёд вручную — подставляем старые метки
        cb._failures = [time.monotonic() - 2.0, time.monotonic() - 2.0]
        assert cb.failures_count == 0


# --------------------------------------------------------------------------- #
# PluginRunner — unit (без subprocess)
# --------------------------------------------------------------------------- #


class TestPluginRunnerUnit:
    def test_call_without_start_raises(self) -> None:
        runner = PluginRunner()
        with pytest.raises(RuntimeError, match="not started"):
            import asyncio

            asyncio.run(runner.call("echo", {}))

    def test_circuit_breaker_open_raises(self) -> None:
        runner = PluginRunner(config=PluginRunnerConfig(circuit_breaker_failures=1))
        runner._circuit_breaker.record_failure()  # force open
        assert runner._circuit_breaker.is_open()
        with pytest.raises(PluginDisabledError):
            import asyncio

            asyncio.run(runner.call("echo", {}))

    def test_health_before_start(self) -> None:
        runner = PluginRunner()
        h = runner.health()
        assert h["alive"] is False
        assert h["disabled"] is False

    def test_reset_circuit_breaker(self) -> None:
        runner = PluginRunner(config=PluginRunnerConfig(circuit_breaker_failures=1))
        runner._circuit_breaker.record_failure()
        assert runner._circuit_breaker.is_open()
        runner.reset_circuit_breaker()
        assert not runner._circuit_breaker.is_open()

    def test_non_json_payload_raises_type_error(self) -> None:
        runner = PluginRunner()
        # Подставим мок-очереди, чтобы не нужен был процесс
        mock_q: MagicMock = MagicMock()
        runner._request_queue = mock_q
        runner._response_queue = mock_q
        runner._process = MagicMock()

        import asyncio

        with pytest.raises(TypeError):
            asyncio.run(runner.call("echo", {"obj": object()}))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# PluginRunner — интеграционные (реальный subprocess)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestPluginRunnerIntegration:
    async def test_happy_path_sync(self) -> None:
        runner = PluginRunner()
        runner.start("floweon_sdk._test_echo.EchoPlugin")
        try:
            result = await runner.call("echo", {"key": "value"})
            assert result == {"key": "value"}
        finally:
            await runner.stop()

    async def test_happy_path_async_method(self) -> None:
        runner = PluginRunner()
        runner.start("floweon_sdk._test_echo.AsyncEchoPlugin")
        try:
            result = await runner.call("echo", {"x": 1})
            assert result == {"x": 1}
        finally:
            await runner.stop()

    async def test_plugin_error_raises_call_error(self) -> None:
        runner = PluginRunner()
        runner.start("floweon_sdk._test_echo.BrokenPlugin")
        try:
            with pytest.raises(PluginCallError, match="deliberate error"):
                await runner.call("broken", {})
        finally:
            await runner.stop()

    async def test_timeout_raises_and_kills_process(self) -> None:
        runner = PluginRunner(config=PluginRunnerConfig(timeout_sec=1))
        runner.start("floweon_sdk._test_echo.SlowPlugin")
        with pytest.raises(PluginTimeoutError):
            await runner.call("slow", {})
        assert runner._process is None

    async def test_health_after_start(self) -> None:
        runner = PluginRunner()
        runner.start("floweon_sdk._test_echo.EchoPlugin")
        try:
            h = runner.health()
            assert h["alive"] is True
            assert h["failures_count"] == 0
            assert h["disabled"] is False
        finally:
            await runner.stop()

    async def test_stop_graceful(self) -> None:
        runner = PluginRunner()
        runner.start("floweon_sdk._test_echo.EchoPlugin")
        await runner.stop()
        assert runner._process is None

    async def test_circuit_breaker_triggered_by_repeated_errors(self) -> None:
        runner = PluginRunner(
            config=PluginRunnerConfig(
                circuit_breaker_failures=3,
                circuit_breaker_window_sec=300,
            )
        )
        runner.start("floweon_sdk._test_echo.BrokenPlugin")
        try:
            for _ in range(3):
                with pytest.raises(PluginCallError):
                    await runner.call("broken", {})
            with pytest.raises(PluginDisabledError):
                await runner.call("broken", {})
        finally:
            if runner._process is not None:
                await runner.stop()

    async def test_reset_after_circuit_breaker_enables_calls(self) -> None:
        runner = PluginRunner(
            config=PluginRunnerConfig(
                circuit_breaker_failures=1,
                circuit_breaker_window_sec=300,
            )
        )
        runner.start("floweon_sdk._test_echo.EchoPlugin")
        # Принудительно открываем circuit breaker
        runner._circuit_breaker.record_failure()
        assert runner._circuit_breaker.is_open()
        runner.reset_circuit_breaker()
        result = await runner.call("echo", {"ok": True})
        assert result == {"ok": True}
        await runner.stop()


# --------------------------------------------------------------------------- #
# v0.3.0: loop, полосы, half-open, краш, PluginEntrypoint
# --------------------------------------------------------------------------- #


class TestHalfOpenBreaker:
    def test_half_open_after_cooldown_and_close_on_success(self) -> None:
        cb = CircuitBreaker(max_failures=1, window_sec=300, cooldown_sec=0.05)
        cb.record_failure()
        assert cb.is_open() and cb.state == "open"
        time.sleep(0.06)
        assert not cb.is_open()  # пробный вызов пропущен
        assert cb.is_open()  # второй параллельный — нет, пока проба в полёте
        cb.record_success()
        assert cb.state == "closed"
        assert not cb.is_open()

    def test_failed_probe_reopens(self) -> None:
        cb = CircuitBreaker(max_failures=1, window_sec=300, cooldown_sec=0.05)
        cb.record_failure()
        time.sleep(0.06)
        assert not cb.is_open()
        cb.record_failure()
        assert cb.state == "open"
        assert cb.is_open()


@pytest.mark.asyncio
class TestRunnerV03:
    async def test_state_and_loop_persist_between_calls(self) -> None:
        runner = PluginRunner()
        runner.start("floweon_sdk._test_echo.StatefulPlugin")
        try:
            first = await runner.call("count", {})
            second = await runner.call("count", {})
            assert (first["calls"], second["calls"]) == (1, 2)
            assert first["loop"] == second["loop"]
        finally:
            await runner.stop()

    async def test_fast_lane_not_blocked_by_slow_write(self) -> None:
        import asyncio

        runner = PluginRunner(config=PluginRunnerConfig(max_concurrency=1))
        runner.start("floweon_sdk._test_echo.LanesPlugin")
        try:
            await runner.call("subscribe", {})  # прогрев процесса
            slow = asyncio.create_task(runner.call("write", {"sleep": 2}))
            await asyncio.sleep(0.2)
            started = time.monotonic()
            assert await runner.call("subscribe", {}) == {"accepted": True}
            assert time.monotonic() - started < 1.0
            assert await slow == {"written": True}
        finally:
            await runner.stop()

    async def test_parallel_calls_correlated(self) -> None:
        import asyncio

        runner = PluginRunner()
        runner.start("floweon_sdk._test_echo.AsyncEchoPlugin")
        try:
            results = await asyncio.gather(*(runner.call("echo", {"n": i}) for i in range(20)))
            assert [r["n"] for r in results] == list(range(20))
        finally:
            await runner.stop()

    async def test_breaker_recovers_after_cooldown(self) -> None:
        import asyncio

        runner = PluginRunner(
            config=PluginRunnerConfig(circuit_breaker_failures=2, cooldown_sec=0.3)
        )
        runner.start("floweon_sdk._test_echo.FlakyPlugin")
        try:
            for _ in range(2):
                with pytest.raises(PluginCallError):
                    await runner.call("maybe", {"fail": True})
            with pytest.raises(PluginDisabledError):
                await runner.call("maybe", {})
            assert runner.health()["breaker"] == "open"
            await asyncio.sleep(0.35)
            assert await runner.call("maybe", {}) == {"ok": True}
            assert runner.health()["breaker"] == "closed"
        finally:
            await runner.stop()

    async def test_crash_fails_pending_call(self) -> None:
        runner = PluginRunner(config=PluginRunnerConfig(timeout_sec=20))
        runner.start("floweon_sdk._test_echo.CrashPlugin")
        started = time.monotonic()
        with pytest.raises(PluginCallError, match="died"):
            await runner.call("crash", {})
        assert time.monotonic() - started < 10
        await runner.stop()


@pytest.mark.asyncio
class TestPluginEntrypoint:
    async def test_connector_contract_through_runner(self) -> None:
        runner = PluginRunner()
        runner.start(
            "floweon_sdk.entrypoint.PluginEntrypoint",
            {"connector": "floweon_sdk._test_connector:EchoConnector", "config": {"k": "v"}},
        )
        try:
            assert await runner.call("start", {}) == {"started": True}
            read = await runner.call("read", {"_entity": "item", "_params": {"a": 1}})
            assert read == {"items": [{"entity": "item", "params": {"a": 1}, "config": {"k": "v"}}]}
            written = await runner.call("write", {"_entity": "item", "_data": {"x": 1}})
            assert written == {"entity": "item", "data": {"x": 1}}
            error = await runner.call("write", {"_entity": "boom", "_data": {}})
            assert error["_error"]["type"] == "ValueError"
            assert runner.health()["failures_count"] == 0  # бизнес-ошибка — не сбой плагина
        finally:
            await runner.stop()
