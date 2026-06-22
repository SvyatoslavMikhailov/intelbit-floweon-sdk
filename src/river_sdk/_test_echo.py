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
