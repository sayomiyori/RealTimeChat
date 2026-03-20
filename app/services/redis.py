import logging
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.client import Redis as RedisClient

logger = logging.getLogger(__name__)

_redis_client: Redis | None = None


async def init_redis(redis_url: str) -> None:
    global _redis_client

    if _redis_client is not None:
        return

    client: RedisClient = Redis.from_url(redis_url, decode_responses=True)
    await client.ping()
    _redis_client = client
    logger.info("Redis is connected")


async def close_redis() -> None:
    global _redis_client

    if _redis_client is None:
        return

    await _redis_client.close()
    await _redis_client.connection_pool.disconnect()
    _redis_client = None


async def publish_room_event(room_id: int, payload: dict[str, Any]) -> int:
    if _redis_client is None:
        raise RuntimeError("Redis is not initialized")

    channel = f"chat:room:{room_id}"
    # Keep payload JSON and simple for interoperability.
    import json

    message = json.dumps(payload, ensure_ascii=True)
    return await _redis_client.publish(channel, message)

