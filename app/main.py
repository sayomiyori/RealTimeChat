import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.routers import auth_router, chat_router
from app.services.redis import close_redis, get_redis

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> None:
    logger.info("Starting realtime-chat app")
    await get_redis()
    yield
    logger.info("Stopping realtime-chat app")
    await close_redis()


app = FastAPI(title="realtime-chat", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(chat_router)

