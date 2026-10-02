"""Тестовый ConnectorPlugin для PluginEntrypoint (только для тестовой среды)."""

from typing import Any

from floweon_sdk.connector import ConnectorPlugin


class EchoConnector(ConnectorPlugin):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def read(self, entity: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"entity": entity, "params": params, "config": self.config}]

    async def write(self, entity: str, data: dict[str, Any]) -> dict[str, Any]:
        if entity == "boom":
            raise ValueError("business error")
        return {"entity": entity, "data": data}
