from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RoomCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class RoomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    owner_id: int
    created_at: datetime

