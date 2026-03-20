from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.room import Room
    from app.models.user import User


class Message(Base):
    __tablename__ = "messages"

    content: Mapped[str] = mapped_column(Text, nullable=False)

    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rooms.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    room: Mapped["Room"] = relationship(back_populates="messages", lazy="joined")
    user: Mapped["User"] = relationship(back_populates="messages", lazy="joined")

    def to_dict(self) -> dict[str, object]:
        username = self.user.username if self.user is not None else None
        return {
            "id": str(self.id),
            "content": self.content,
            "room_id": str(self.room_id),
            "user_id": str(self.user_id),
            "created_at": self.created_at.isoformat(),
            "username": username,
        }

    @classmethod
    async def create(
        cls,
        *,
        session: AsyncSession,
        room_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
    ) -> "Message":
        """Create and persist a message using AsyncSession."""
        message = cls(room_id=room_id, user_id=user_id, content=content)
        session.add(message)
        await session.commit()
        await session.refresh(message)
        return message

