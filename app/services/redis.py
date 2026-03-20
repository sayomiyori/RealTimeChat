import json
import logging
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: Redis | None = None


async def get_redis() -> Redis:
    """Return singleton async Redis connection."""
    global _redis_client

    if _redis_client is not None:
        return _redis_client

    client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    await client.ping()
    _redis_client = client
    logger.info("Redis connected: %s", settings.REDIS_URL)
    return client


async def publish(room_id: str, message: dict[str, Any]) -> None:
    """Publish JSON message into room channel."""
    redis = await get_redis()
    channel = f"chat:room:{room_id}"
    payload = json.dumps(message, ensure_ascii=True)
    await redis.publish(channel, payload)


async def subscribe(room_id: str) -> PubSub:
    """Create PubSub, subscribe to room channel, return pubsub for listen()."""
    redis = await get_redis()
    channel = f"chat:room:{room_id}"

    pubsub: PubSub = redis.pubsub()
    await pubsub.subscribe(channel)
    return pubsub


async def close_redis() -> None:
    """Graceful shutdown for Redis singleton."""
    global _redis_client

    if _redis_client is None:
        return

    try:
        await _redis_client.close()
    finally:
        await _redis_client.connection_pool.disconnect()
        _redis_client = None


__all__ = ["get_redis", "publish", "subscribe", "close_redis"]

