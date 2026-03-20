import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_access_token, get_current_user, oauth2_scheme
from app.core.db import async_session_maker, get_db
from app.models.message import Message
from app.models.room import Room
from app.models.user import User
from app.schemas.message import MessageCreateRequest, MessageOut
from app.schemas.room import RoomCreateRequest, RoomOut
from app.services.connection import ConnectionManager
from app.services.redis import publish_room_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])
manager = ConnectionManager()


@router.post("/rooms", response_model=RoomOut)
async def create_room(
    payload: RoomCreateRequest,
    session: AsyncSession = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> RoomOut:
    current_user = await get_current_user(token=token, db=session)
    existing = await session.execute(select(Room).where(Room.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Room already exists")

    room = Room(name=payload.name, owner_id=current_user.id)
    session.add(room)
    await session.commit()
    await session.refresh(room)
    return RoomOut.model_validate(room)


@router.get("/rooms/{room_id}", response_model=RoomOut)
async def get_room(
    room_id: int,
    session: AsyncSession = Depends(get_db),
) -> RoomOut:
    result = await session.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    return RoomOut.model_validate(room)


@router.websocket("/ws/rooms/{room_id}")
async def websocket_room(
    websocket: WebSocket,
    room_id: int,
) -> None:
    # Auth for WS: pass `Authorization: Bearer <token>` header.
    auth_header = websocket.headers.get("Authorization")
    if auth_header is None or not auth_header.lower().startswith("bearer "):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    token = auth_header.split(" ", 1)[1].strip()
    try:
        payload: dict[str, Any] = await decode_access_token(token)
        subject = payload.get("sub")
        if subject is None:
            raise ValueError("Missing token subject")
        user_id = int(subject)
    except Exception as exc:  # noqa: BLE001
        logger.warning("WS auth failed: %s", exc)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

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

        await manager.connect(room_id, websocket)
        try:
            while True:
                incoming = await websocket.receive_json()
                msg_in = MessageCreateRequest.model_validate(incoming)

                message = Message(room_id=room_id, user_id=user.id, content=msg_in.content)
                session.add(message)
                await session.commit()
                await session.refresh(message)

                msg_out = MessageOut.model_validate(message)
                await manager.broadcast(room_id, msg_out.model_dump())
                await publish_room_event(room_id, {"type": "message", "data": msg_out.model_dump()})
        except WebSocketDisconnect:
            await manager.disconnect(room_id, websocket)
        except Exception:  # noqa: BLE001
            logger.exception("WebSocket room handler error")
            await manager.disconnect(room_id, websocket)
            await websocket.close()


__all__ = ["router"]

