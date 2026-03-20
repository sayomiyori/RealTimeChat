import asyncio
import json
import logging
from typing import Any
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, get_current_user_ws, oauth2_scheme
from app.core.db import get_db
from app.models.message import Message
from app.models.room import Room
from app.models.user import User
from app.schemas.message import MessageCreate, MessageResponse
from app.schemas.room import RoomCreate, RoomResponse
from app.services.connection import manager
from app.services.redis import publish, subscribe

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


async def get_current_user_dep(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> User:
    # Wrapper because `get_current_user(token, db)` doesn't embed FastAPI Depends defaults.
    return await get_current_user(token=token, db=db)


def _message_to_dict(message: Message) -> dict[str, Any]:
    username = message.user.username if message.user is not None else ""
    return {
        "id": str(message.id),
        "content": message.content,
        "username": username,
        "room_id": str(message.room_id),
        "created_at": message.created_at.isoformat(),
    }


@router.post("/rooms", response_model=RoomResponse)
async def create_room(
    payload: RoomCreate,
    session: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: User = Depends(get_current_user_dep),  # noqa: B008
) -> RoomResponse:
    _ = current_user  # room creation is protected by JWT

    existing = await session.execute(select(Room).where(Room.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Room already exists")

    room = Room(name=payload.name, description=payload.description)
    session.add(room)
    await session.commit()
    await session.refresh(room)

    online_count = manager.get_room_count(str(room.id))
    return RoomResponse(
        id=room.id,
        name=room.name,
        description=room.description,
        online_count=online_count,
    )


@router.get("/rooms", response_model=list[RoomResponse])
async def list_rooms(
    session: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: User = Depends(get_current_user_dep),  # noqa: B008
) -> list[RoomResponse]:
    _ = current_user  # list is protected by JWT

    rooms_result = await session.execute(select(Room))
    rooms = rooms_result.scalars().all()

    return [
        RoomResponse(
            id=room.id,
            name=room.name,
            description=room.description,
            online_count=manager.get_room_count(str(room.id)),
        )
        for room in rooms
    ]


@router.get("/rooms/{room_id}/history", response_model=list[MessageResponse])
async def room_history(
    room_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: User = Depends(get_current_user_dep),  # noqa: B008
) -> list[MessageResponse]:
    _ = current_user  # history is protected by JWT

    room_result = await session.execute(select(Room).where(Room.id == room_id))
    room = room_result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    stmt = (
        select(Message)
        .where(Message.room_id == room_id)
        .order_by(Message.created_at)
        .limit(limit)
        .offset(offset)
    )
    messages_result = await session.execute(stmt)
    messages = messages_result.scalars().all()

    return [
        MessageResponse(
            id=message.id,
            content=message.content,
            username=message.user.username if message.user is not None else "",
            room_id=message.room_id,
            created_at=message.created_at,
        )
        for message in messages
    ]


@router.websocket("/ws/{room_id}")
async def websocket_room(
    websocket: WebSocket,
    room_id: UUID,
    token: str = Query(..., description="JWT access token"),
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> None:
    """WebSocket chat for a room.

    Token is accepted as query parameter: `?token=...`.
    """

    room_id_str = str(room_id)

    current_user = await get_current_user_ws(token=token, db=session)
    if current_user is None:
        await websocket.close(code=4001)
        return

    room_result = await session.execute(select(Room).where(Room.id == room_id))
    if room_result.scalar_one_or_none() is None:
        await websocket.close(code=4004)
        return

    await manager.connect(websocket, room_id_str)

    # Load last 50 messages and send as WS history.
    history_stmt = (
        select(Message)
        .where(Message.room_id == room_id)
        .order_by(Message.created_at.desc())
        .limit(50)
    )
    history_result = await session.execute(history_stmt)
    history_messages_desc = history_result.scalars().all()
    history_messages_asc = list(reversed(history_messages_desc))
    history_payload = {
        "type": "history",
        "messages": [_message_to_dict(m) for m in history_messages_asc],
    }
    await websocket.send_text(json.dumps(history_payload, ensure_ascii=True))

    pubsub = await subscribe(room_id_str)

    current_user_id_str = str(current_user.id)

    async def redis_listener() -> None:
        try:
            while True:
                redis_msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if redis_msg is None:
                    continue

                data = redis_msg.get("data")
                if data is None:
                    continue
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")

                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("Redis pubsub payload is not JSON: %s", data)
                    continue

                msg_type = payload.get("type")
                if msg_type == "message":
                    msg_data = payload.get("data")
                    if (
                        isinstance(msg_data, dict)
                        and msg_data.get("user_id") == current_user_id_str
                    ):
                        # Avoid duplicating sender echo.
                        continue
                try:
                    await websocket.send_text(data)
                except WebSocketDisconnect:
                    return
                except RuntimeError:
                    # Connection might be closing.
                    return
        except asyncio.CancelledError:
            return

    redis_task = asyncio.create_task(redis_listener())

    # Announce join.
    await publish(
        room_id_str,
        {"type": "system", "content": f"{current_user.username} вошёл в чат"},
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                incoming = json.loads(raw)
                msg_type = incoming.get("type")
            except json.JSONDecodeError:
                logger.warning("WS message JSON decode error: %s", raw)
                continue

            if msg_type == "message":
                data_part = incoming.get("data")
                data_for_validation: dict[str, Any] = (
                    data_part if isinstance(data_part, dict) else incoming
                )
                try:
                    msg_in = MessageCreate.model_validate(data_for_validation)
                except ValidationError as exc:
                    logger.warning("WS message validation error: %s", exc)
                    continue

                created_message = await Message.create(
                    session=session,
                    room_id=room_id,
                    user_id=current_user.id,
                    content=msg_in.content,
                )

                message_dict = {
                    "id": str(created_message.id),
                    "content": created_message.content,
                    "username": current_user.username,
                    "room_id": str(created_message.room_id),
                    "created_at": created_message.created_at.isoformat(),
                    "user_id": current_user_id_str,
                }

                redis_payload = {"type": "message", "data": message_dict}
                await publish(room_id_str, redis_payload)
                await websocket.send_text(json.dumps(redis_payload, ensure_ascii=True))

            elif msg_type == "typing":
                data_part = incoming.get("data")
                typing_data: dict[str, Any] = data_part if isinstance(data_part, dict) else incoming
                is_typing = bool(typing_data.get("is_typing"))
                await publish(
                    room_id_str,
                    {"type": "typing", "username": current_user.username, "is_typing": is_typing},
                )
            else:
                # Unknown message type - ignore.
                continue

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket chat handler error")
    finally:
        # Shield cleanup from anyio task cancellation so that the asyncpg
        # connection is fully closed before the event loop tears down.
        # Without this, a pending asyncio.Future from this loop leaks into the
        # next TestClient loop and causes "Future attached to a different loop".
        with anyio.CancelScope(shield=True):
            redis_task.cancel()
            try:
                await redis_task
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass
            except Exception:
                logger.exception("Redis listener task failed during cleanup")

            await manager.disconnect(websocket, room_id_str)

            await publish(
                room_id_str,
                {"type": "system", "content": f"{current_user.username} вышел из чата"},
            )

            try:
                await pubsub.unsubscribe()
            except Exception:
                pass

            await session.close()



__all__ = ["router"]

