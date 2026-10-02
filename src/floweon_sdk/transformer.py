from abc import abstractmethod
from typing import Any

from floweon_sdk.connector import BasePlugin


class TransformerPlugin(BasePlugin):
    """Базовый класс для плагинов-трансформеров (ADR-006).

    Трансформер расширяет mapping-engine: преобразует сообщения между форматами.
    """

    @abstractmethod
    async def transform(
        self,
        message: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Преобразовать сообщение."""
        ...
