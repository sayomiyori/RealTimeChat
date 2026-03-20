from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.message import Message


class User(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    messages: Mapped[list[Message]] = relationship(back_populates="user", lazy="selectin")

    def to_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "username": self.username,
            "email": self.email,
            "hashed_password": self.hashed_password,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
        }

