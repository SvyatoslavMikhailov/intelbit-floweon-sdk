from abc import ABC, abstractmethod
from typing import Any

from river_sdk.manifest import PluginManifest


class PluginContext:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config


class PluginHealth:
    def __init__(self, healthy: bool, message: str = "") -> None:
        self.healthy = healthy
        self.message = message


class BasePlugin(ABC):
    """Базовый класс плагина (ADR-006 v1.1).

    Контракт subprocess-изоляции: плагин может быть убит в любой момент;
    не держи открытые соединения между вызовами; сбрасывай состояние при каждом call.
    Все методы должны быть idempotent: повторный вызов с тем же payload даёт тот же результат.
    """

    manifest: PluginManifest

    async def init(self, context: PluginContext) -> None:  # noqa: B027
        pass

    @abstractmethod
    async def start(self) -> None:
        """Инициализировать ресурсы. Idempotent; не держи соединения между вызовами."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Освободить ресурсы. Может быть вызван в любой момент без предупреждения."""
        ...

    async def health_check(self) -> PluginHealth:
        return PluginHealth(healthy=True)

    async def reload(self, new_config: dict[str, Any]) -> None:
        await self.stop()
        await self.init(PluginContext(new_config))
        await self.start()


class ConnectorPlugin(BasePlugin, ABC):
    """Базовый класс для плагинов-коннекторов (ADR-006).

    Коннектор обеспечивает чтение/запись во внешнюю систему (1C, Ozon, HTTP и др.).
    Все методы idempotent; не держи соединения между вызовами; flush при каждом call.
    """

    @abstractmethod
    async def read(self, entity: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Прочитать записи из внешней системы. Idempotent."""
        ...

    @abstractmethod
    async def write(self, entity: str, data: dict[str, Any]) -> dict[str, Any]:
        """Записать данные во внешнюю систему. Idempotent."""
        ...
