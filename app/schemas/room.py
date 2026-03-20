from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RoomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class RoomResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    online_count: int


