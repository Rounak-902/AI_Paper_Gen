from __future__ import annotations

import datetime as dt
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class NotificationResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class NotificationOut(NotificationResponse):
    id: uuid.UUID
    code: str
    type: str
    title: str
    body: Optional[str] = None
    link: Optional[str] = None
    entity: Optional[str] = None
    entity_id: Optional[uuid.UUID] = None
    read_at: Optional[dt.datetime] = None
    created_at: dt.datetime


class NotificationPage(NotificationResponse):
    items: list[NotificationOut]
    next_cursor: Optional[str] = None
    has_next: bool


class UnreadCountOut(NotificationResponse):
    unread_count: int = Field(ge=0)


class MarkAllReadOut(NotificationResponse):
    marked_read: int = Field(ge=0)


__all__ = [
    "MarkAllReadOut",
    "NotificationOut",
    "NotificationPage",
    "UnreadCountOut",
]
