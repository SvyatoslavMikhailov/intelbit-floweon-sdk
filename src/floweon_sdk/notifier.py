from abc import abstractmethod
from typing import Any

from floweon_sdk.connector import BasePlugin


class NotifierPlugin(BasePlugin):
    """Базовый класс для плагинов-нотификаторов (ADR-006).

    Нотификатор расширяет notification-service: доставляет уведомления в канал.
    """

    @abstractmethod
    async def send(
        self,
        recipient: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Отправить уведомление. Вернуть True при успехе."""
        ...
