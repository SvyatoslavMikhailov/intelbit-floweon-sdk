"""Контракт плагинов-нотификаторов (ADR-006).

v0.3.1: уведомление — структурированный словарь (`notify`) с ключом дедупликации и
маршрутом, плюс `status` внешней сущности (задача закрыта?) — чтобы ядро могло
вести «одна открытая задача на одну проблему». `send` оставлен как устаревшая обёртка.
"""

import warnings
from abc import abstractmethod
from typing import Any, Literal, NotRequired, TypedDict

from floweon_sdk.connector import BasePlugin

NotificationKind = Literal["inbox_task", "workflow_failed", "dlq", "digest", "message"]


class NotificationRoute(TypedDict):
    """Кому и в какие сроки: ответственный, группа (проект), дедлайн в часах."""

    responsible_id: str
    group_id: NotRequired[str | None]
    deadline_hours: NotRequired[int]


class Notification(TypedDict):
    """Уведомление ядра для нотификатора.

    action в ответе определяет ядро через `mode`: create — новая сущность (задача),
    comment — дописать в существующую `external_id`, send — разовое сообщение (сводка).
    """

    kind: NotificationKind
    dedup_key: str
    title: str
    body: str
    severity: str
    route: NotificationRoute
    mode: NotRequired[Literal["create", "comment", "send"]]
    external_id: NotRequired[str]
    crm_binding: NotRequired[str]
    context: NotRequired[dict[str, Any]]


class NotifyResult(TypedDict):
    action: Literal["created", "commented", "sent", "skipped"]
    external_id: NotRequired[str]


class NotifierPlugin(BasePlugin):
    """Базовый класс для плагинов-нотификаторов (ADR-006)."""

    @abstractmethod
    async def notify(self, notification: Notification) -> NotifyResult:
        """Доставить уведомление: создать сущность, дописать комментарий или отправить."""
        ...

    async def status(self, external_id: str) -> dict[str, Any]:
        """Состояние внешней сущности: {"open": bool}. По умолчанию — открыта."""
        return {"open": True}

    async def find(self, dedup_key: str) -> dict[str, Any]:
        """Открытая сущность, созданная ранее для dedup_key: {"external_id": str | None}.

        Делает создание идемпотентным: если ответ на создание потерян (таймаут, 5xx
        прокси), ядро перед повтором ищет уже созданную сущность. По умолчанию —
        поиск не поддержан (None).
        """
        return {"external_id": None}

    async def send(
        self,
        recipient: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Устаревший контракт v0.2: разовое сообщение получателю. Используйте notify."""
        warnings.warn(
            "NotifierPlugin.send устарел — используйте notify()", DeprecationWarning, stacklevel=2
        )
        result = await self.notify(
            {
                "kind": "message",
                "mode": "send",
                "dedup_key": "",
                "title": message[:250],
                "body": message,
                "severity": "info",
                "route": {"responsible_id": recipient},
                "context": metadata or {},
            }
        )
        return result.get("action") != "skipped"
