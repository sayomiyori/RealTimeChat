from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content: str
    username: str
    room_id: UUID
    created_at: datetime


class WsMessage(BaseModel):
    # Формат, который шлём клиентам по WebSocket.
    event: str
    data: dict[str, Any]

