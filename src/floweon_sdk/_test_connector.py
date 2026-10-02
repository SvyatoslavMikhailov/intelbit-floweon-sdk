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


class EchoNotifier:
    """Тестовый нотификатор для PluginEntrypoint."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.closed = set(config.get("closed", []))

    async def notify(self, notification: dict[str, Any]) -> dict[str, Any]:
        if notification.get("title") == "boom":
            raise ConnectionError("channel down")
        mode = notification.get("mode", "create")
        return {"action": "created" if mode == "create" else "commented", "external_id": "7"}

    async def status(self, external_id: str) -> dict[str, Any]:
        return {"open": external_id not in self.closed}

    async def find(self, dedup_key: str) -> dict[str, Any]:
        return {"external_id": "7" if dedup_key == "known" else None}
