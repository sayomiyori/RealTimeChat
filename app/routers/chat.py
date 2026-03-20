import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_access_token, get_current_user, oauth2_scheme
from app.core.db import async_session_maker, get_db
from app.models.message import Message
from app.models.room import Room
from app.models.user import User
from app.schemas.message import MessageCreate, MessageResponse
from app.schemas.room import RoomCreate, RoomResponse
from app.services.connection import manager
from app.services.redis import publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


async def get_current_user_dep(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
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
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dep),
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
    return RoomResponse(id=room.id, name=room.name, description=room.description, online_count=online_count)


@router.get("/rooms", response_model=list[RoomResponse])
async def list_rooms(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dep),
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
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dep),
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


@router.websocket("/ws/rooms/{room_id}")
async def websocket_room(
    websocket: WebSocket,
    room_id: UUID,
) -> None:
    # Auth for WS: pass `Authorization: Bearer <token>` header.
    auth_header = websocket.headers.get("Authorization")
    if auth_header is None or not auth_header.lower().startswith("bearer "):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    token = auth_header.split(" ", 1)[1].strip()
    try:
        payload: dict[str, Any] = await decode_access_token(token)
        user_id_value = payload.get("user_id") or payload.get("sub")
        if user_id_value is None:
            raise ValueError("Missing token subject")
        user_id = UUID(str(user_id_value))
    except Exception as exc:  # noqa: BLE001
        logger.warning("WS auth failed: %s", exc)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    room_id_str = str(room_id)

    async with async_session_maker() as session:
        room_result = await session.execute(select(Room).where(Room.id == room_id))
        room = room_result.scalar_one_or_none()
        if room is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        user_result = await session.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await manager.connect(websocket, room_id_str)
        try:
            while True:
                incoming = await websocket.receive_json()
                msg_in = MessageCreate.model_validate(incoming)

                message = Message(room_id=room_id, user_id=user.id, content=msg_in.content)
                session.add(message)
                await session.commit()
                await session.refresh(message)

                message_dict = _message_to_dict(message)
                message_text = json.dumps(message_dict, ensure_ascii=True)
                await manager.broadcast(message_text, room_id_str)
                await publish(room_id_str, {"type": "message", "data": message_dict})
        except WebSocketDisconnect:
            await manager.disconnect(websocket, room_id_str)
        except Exception:  # noqa: BLE001
            logger.exception("WebSocket room handler error")
            await manager.disconnect(websocket, room_id_str)
            await websocket.close()


__all__ = ["router"]

