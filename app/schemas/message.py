from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    room_id: int
    user_id: int
    content: str
    created_at: datetime


class WsMessage(BaseModel):
    # Формат, который шлём клиентам по WebSocket.
    event: str
    data: dict[str, Any]

