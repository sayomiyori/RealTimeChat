import logging

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # room_id -> active WebSockets in this room
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room_id: str) -> None:
        await websocket.accept()

        conns = self._connections.setdefault(room_id, [])
        if websocket not in conns:
            conns.append(websocket)

        logger.info("WS connect: room_id=%s connections=%s", room_id, len(conns))

    async def disconnect(self, websocket: WebSocket, room_id: str) -> None:
        conns = self._connections.get(room_id)
        if not conns:
            return

        before = len(conns)
        try:
            conns.remove(websocket)
        except ValueError:
            # Already removed.
            return

        if not conns:
            self._connections.pop(room_id, None)

        logger.info("WS disconnect: room_id=%s before=%s after=%s", room_id, before, len(conns))

    async def broadcast(
        self,
        message: str,
        room_id: str,
        exclude: WebSocket | None = None,
    ) -> None:
        conns = self._connections.get(room_id)
        if not conns:
            return

        to_remove: list[WebSocket] = []
        for conn in list(conns):
            if exclude is not None and conn is exclude:
                continue
            try:
                await conn.send_text(message)
            except WebSocketDisconnect:
                to_remove.append(conn)
            except Exception:  # noqa: BLE001
                logger.exception("WebSocket broadcast failed: room_id=%s", room_id)
                to_remove.append(conn)

        for conn in to_remove:
            await self.disconnect(conn, room_id)

    def get_room_count(self, room_id: str) -> int:
        conns = self._connections.get(room_id)
        return len(conns) if conns is not None else 0

# Singleton instance used across the app.
manager = ConnectionManager()

