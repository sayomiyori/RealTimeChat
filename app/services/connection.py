import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}

    async def connect(self, room_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(room_id, set()).add(websocket)

    async def disconnect(self, room_id: int, websocket: WebSocket) -> None:
        conns = self._connections.get(room_id)
        if conns is None:
            return
        conns.discard(websocket)
        if not conns:
            self._connections.pop(room_id, None)

    async def broadcast(self, room_id: int, message: dict[str, Any]) -> None:
        conns = self._connections.get(room_id)
        if not conns:
            return

        to_remove: list[WebSocket] = []
        for conn in list(conns):
            try:
                await conn.send_json(message)
            except WebSocketDisconnect:
                to_remove.append(conn)
            except Exception:  # noqa: BLE001
                logger.exception("WebSocket broadcast failed")
                to_remove.append(conn)

        for conn in to_remove:
            await self.disconnect(room_id, conn)

