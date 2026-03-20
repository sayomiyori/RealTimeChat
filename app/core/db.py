from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.base import Base

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
async_session_maker = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

target_metadata = Base.metadata


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


__all__ = ["Base", "async_session_maker", "engine", "get_db", "target_metadata"]

