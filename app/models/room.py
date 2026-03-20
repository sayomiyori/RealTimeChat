from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.message import Message


class Room(Base):
    __tablename__ = "rooms"

    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="room",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
        }

