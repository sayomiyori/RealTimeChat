from __future__ import annotations

import json
from typing import Any, Generator

import pytest
from httpx import AsyncClient
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app as fastapi_app


@pytest.fixture(scope="module")
def ws_client() -> Generator[TestClient, None, None]:
    """One TestClient (one event loop) shared across all WebSocket tests in this
    module.  This prevents asyncpg Futures from one test's loop leaking into the
    next test's loop and causing "Future attached to a different loop" errors."""
    with TestClient(fastapi_app) as client:
        yield client


def test_websocket_connect_valid_token(
    ws_client: TestClient,
    auth_headers: dict[str, str],
    test_room: dict[str, Any],
) -> None:
    token = auth_headers["Authorization"].split(" ", 1)[1]
    ws_url = f"/ws/{test_room['id']}?token={token}"

    with ws_client.websocket_connect(ws_url) as websocket:
        raw = websocket.receive_text()
        payload = json.loads(raw)
        assert payload["type"] == "history"
        assert isinstance(payload["messages"], list)


def test_websocket_connect_invalid_token(
    ws_client: TestClient,
    test_room: dict[str, Any],
) -> None:
    ws_url = f"/ws/{test_room['id']}?token=invalid"

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ws_client.websocket_connect(ws_url):
            pass
    assert exc_info.value.code == 4001


def test_websocket_send_message(
    ws_client: TestClient,
    auth_headers: dict[str, str],
    test_room: dict[str, Any],
) -> None:
    token = auth_headers["Authorization"].split(" ", 1)[1]
    ws_url = f"/ws/{test_room['id']}?token={token}"

    with ws_client.websocket_connect(ws_url) as websocket:
        websocket.receive_text()  # history
        websocket.send_text(json.dumps({"type": "message", "data": {"content": "hello"}}))
        raw = websocket.receive_text()
        payload = json.loads(raw)
        assert payload["type"] == "message"
        assert payload["data"]["content"] == "hello"
        assert "username" in payload["data"]


async def test_websocket_typing_event_not_saved(
    ws_client: TestClient,
    auth_headers: dict[str, str],
    test_room: dict[str, Any],
    async_client: AsyncClient,
) -> None:
    token = auth_headers["Authorization"].split(" ", 1)[1]
    ws_url = f"/ws/{test_room['id']}?token={token}"

    with ws_client.websocket_connect(ws_url) as websocket:
        websocket.receive_text()  # history
        websocket.send_text(json.dumps({"type": "typing", "data": {"is_typing": True}}))

    room_id = str(test_room["id"])
    resp = await async_client.get(
        f"/rooms/{room_id}/history",
        headers=auth_headers,
        params={"limit": 50, "offset": 0},
    )
    assert resp.status_code == 200
    assert resp.json() == []
