"""PluginEntrypoint — мост PluginRunner ↔ ConnectorPlugin внутри дочернего процесса.

PluginRunner (SDK) создаёт плагин как `cls(**init_kwargs)` и вызывает `method(payload)`
с одним JSON-словарём. ConnectorPlugin же ожидает `cls(config)`, `read(entity, params)`,
`write(entity, data)`, `subscribe(headers, body: bytes)` и возвращает списки.
Адаптер приводит одно к другому и гарантирует JSON-совместимый результат.

Отказ вебхука возвращается как данные, а не исключение: любое исключение в
PluginRunner — сбой для circuit breaker, и пять мусорных POST из интернета
отключили бы настоящий коннектор до рестарта.
"""

from __future__ import annotations

import base64
import importlib
import inspect
import json
from typing import Any

from floweon_sdk.connector import PluginHealth

_REJECT_401_SUFFIXES = ("ValidationError", "SignatureError", "AuthError")


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _load_class(target: str) -> Any:
    module_path, _, class_name = target.partition(":")
    if not class_name:
        module_path, _, class_name = target.rpartition(".")
    return getattr(importlib.import_module(module_path), class_name)


_TRANSIENT_NAMES = (
    "Timeout",
    "TimeoutError",
    "TransportError",
    "ConnectError",
    "QueryLimitExceeded",
)


def _is_transient(exc: Exception) -> bool:
    """Временная ли ошибка: сеть, таймаут, 5xx/429, лимит запросов."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status >= 500 or status == 429):
        return True
    if getattr(exc, "code", None) == "transport_error":  # клиент Bitrix24
        return True
    return any(type(c).__name__.endswith(_TRANSIENT_NAMES) for c in (exc, exc.__cause__) if c)


_NOT_DELIVERED_NAMES = ("ConnectError", "ConnectionRefusedError", "QueryLimitExceeded")


def _not_delivered(exc: Exception) -> bool:
    """Запрос заведомо не дошёл до внешней системы — повтор записи безопасен.

    Таймаут и 5xx сюда не входят: запись могла выполниться (контрагент создан, ответ
    потерян), и повтор дал бы дубль или ложный отказ «уже существует».
    """
    if isinstance(exc, ConnectionRefusedError):
        return True
    if getattr(exc, "status_code", None) == 429:
        return True
    return any(type(c).__name__.endswith(_NOT_DELIVERED_NAMES) for c in (exc, exc.__cause__) if c)


def _error(exc: Exception) -> dict[str, Any]:
    """Ошибка коннектора → данные: исключение в PluginRunner считалось бы сбоем
    для circuit breaker, и бизнес-отказы (дубль контрагента) выключили бы коннектор."""
    return {
        "_error": {
            "type": type(exc).__name__,
            "message": str(exc)[:2000],
            "transient": _is_transient(exc),
            # Для write: повтор безопасен только если запрос не дошёл.
            "retry_safe_write": _not_delivered(exc),
        }
    }


class PluginEntrypoint:
    """Точка входа PluginRunner для ConnectorPlugin (контракт ADR-006, SDK v0.3.0).

    Ядро запускает `floweon_sdk.entrypoint.PluginEntrypoint` с аргументами
    `connector="module:Class"` и `config={...}`; сам коннектор — `cls(config)`.

    Args:
        connector: Класс коннектора в формате entry point (`module:Class`).
        config: Конфигурация коннектора (секреты уже подставлены ядром).
    """

    def __init__(self, connector: str, config: dict[str, Any]) -> None:
        self._connector = _load_class(connector)(config)

    async def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._connector.start()
        return {"started": True}

    async def stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._connector.stop()
        return {"stopped": True}

    async def health(self, payload: dict[str, Any]) -> dict[str, Any]:
        result: PluginHealth = await self._connector.health_check()
        return {"healthy": bool(result.healthy), "message": str(result.message)}

    async def read(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            items = await self._connector.read(
                str(payload.get("_entity", "")), dict(payload.get("_params") or {})
            )
        except Exception as exc:
            return _error(exc)
        return {"items": _jsonable(items)}

    async def write(self, payload: dict[str, Any]) -> dict[str, Any]:
        # _data — запись шага (элемент батча); _params — прежний контракт.
        data = payload.get("_data")
        if data is None:
            data = payload.get("_params") or {}
        try:
            result = await self._connector.write(str(payload.get("_entity", "")), dict(data))
        except Exception as exc:
            return _error(exc)
        return dict(_jsonable(result))

    async def notify(self, payload: dict[str, Any]) -> dict[str, Any]:
        """NotifierPlugin.notify; ошибка канала — данными (как у read/write)."""
        try:
            result = await self._connector.notify(dict(payload))
        except Exception as exc:
            return _error(exc)
        return dict(_jsonable(result))

    async def status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """NotifierPlugin.status(external_id)."""
        try:
            result = await self._connector.status(str(payload.get("external_id", "")))
        except Exception as exc:
            return _error(exc)
        return dict(_jsonable(result))

    async def find(self, payload: dict[str, Any]) -> dict[str, Any]:
        """NotifierPlugin.find(dedup_key) — поиск ранее созданной сущности."""
        try:
            result = await self._connector.find(str(payload.get("dedup_key", "")))
        except Exception as exc:
            return _error(exc)
        return dict(_jsonable(result))

    async def subscribe(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Валидировать входящий вебхук → {"accepted": bool, ...}; никогда не бросает."""
        subscribe = getattr(self._connector, "subscribe", None)
        if subscribe is None:
            return {"accepted": False, "status": 404, "error": "connector has no webhooks"}
        headers = {str(k): str(v) for k, v in dict(payload.get("headers") or {}).items()}
        body = base64.b64decode(str(payload.get("body_b64", "")))
        kwargs: dict[str, Any] = {}
        if "peer_ip" in inspect.signature(subscribe).parameters:
            kwargs["peer_ip"] = payload.get("peer_ip")
        try:
            event = await subscribe(headers, body, **kwargs)
        except Exception as exc:
            # Только имя класса: текст исключения коннектора может цитировать тело.
            name = type(exc).__name__
            status = 401 if name.endswith(_REJECT_401_SUFFIXES) else 400
            return {"accepted": False, "status": status, "error": name}
        return {"accepted": True, "event": _jsonable(event)}
