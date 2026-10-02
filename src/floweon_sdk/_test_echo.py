"""Минимальные плагины для тестов PluginRunner (только для тестовой среды)."""

import asyncio
from typing import Any


class EchoPlugin:
    """Возвращает payload обратно — happy-path тест."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    def echo(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload


class AsyncEchoPlugin:
    """Async-версия EchoPlugin."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def echo(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload


class SlowPlugin:
    """Зависает навсегда — тест timeout."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def slow(self, payload: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(9999)
        return {}


class BrokenPlugin:
    """Всегда бросает исключение — тест PluginCallError."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    def broken(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("plugin deliberate error")


class StatefulPlugin:
    """Состояние и asyncio-примитив живут между вызовами (долгоживущий loop)."""

    def __init__(self, **kwargs: Any) -> None:
        self.calls = 0
        self.lock = asyncio.Lock()

    async def count(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            self.calls += 1
            return {"calls": self.calls, "loop": id(asyncio.get_running_loop())}


class LanesPlugin:
    """Долгая запись в обычной полосе и быстрый subscribe."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def write(self, payload: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(float(payload.get("sleep", 2)))
        return {"written": True}

    async def subscribe(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"accepted": True}


class FlakyPlugin:
    """Ошибка, пока payload не скажет иначе — тест half-open breaker."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    def maybe(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("fail"):
            raise ValueError("flaky")
        return {"ok": True}


class CrashPlugin:
    """Процесс умирает посреди вызова — ожидающий вызов не должен висеть."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    def crash(self, payload: dict[str, Any]) -> dict[str, Any]:
        import os

        os._exit(1)
