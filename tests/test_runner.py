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
