from __future__ import annotations

import json
from typing import Any

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient

from app.main import app as fastapi_app


async def _ws_connect_and_read_first_history(ws_url: str) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        with TestClient(fastapi_app) as client:
            with client.websocket_connect(ws_url) as websocket:
                raw = websocket.receive_text()
                return json.loads(raw)

    return await anyio.to_thread.run_sync(_run)


async def test_websocket_connect_valid_token(
    auth_headers: dict[str, str],
    test_room: dict[str, Any],
) -> None:
    token = auth_headers["Authorization"].split(" ", 1)[1]
    ws_url = f"ws://testserver/ws/{test_room['id']}?token={token}"

    payload = await _ws_connect_and_read_first_history(ws_url)
    assert payload["type"] == "history"
    assert payload["messages"] == []


async def test_websocket_connect_invalid_token(test_room: dict[str, Any]) -> None:
    ws_url = f"ws://testserver/ws/{test_room['id']}?token=invalid"

    def _run() -> int | None:
        with TestClient(fastapi_app) as client:
            try:
                with client.websocket_connect(ws_url) as websocket:
                    websocket.receive_text()
                    return None
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "code", None)
                if code is None:
                    code = getattr(getattr(exc, "response", None), "status_code", None)
                close_code = getattr(exc, "close_code", None)
                return close_code or code

    close_code = await anyio.to_thread.run_sync(_run)
    assert close_code == 4001


async def test_websocket_send_message(
    auth_headers: dict[str, str],
    test_room: dict[str, Any],
) -> None:
    token = auth_headers["Authorization"].split(" ", 1)[1]
    ws_url = f"ws://testserver/ws/{test_room['id']}?token={token}"
    username = test_room.get("username")  # may not exist

    def _run() -> dict[str, Any]:
        with TestClient(fastapi_app) as client:
            with client.websocket_connect(ws_url) as websocket:
                websocket.receive_text()  # history
                websocket.send_text(json.dumps({"type": "message", "data": {"content": "hello"}}))
                raw = websocket.receive_text()
                return json.loads(raw)

    payload = await anyio.to_thread.run_sync(_run)
    assert payload["type"] == "message"
    assert payload["data"]["content"] == "hello"
    assert "username" in payload["data"]


async def test_websocket_typing_event_not_saved(
    auth_headers: dict[str, str],
    test_room: dict[str, Any],
    async_client: AsyncClient,
) -> None:
    token = auth_headers["Authorization"].split(" ", 1)[1]
    ws_url = f"ws://testserver/ws/{test_room['id']}?token={token}"

    def _run() -> None:
        with TestClient(fastapi_app) as client:
            with client.websocket_connect(ws_url) as websocket:
                websocket.receive_text()  # history
                websocket.send_text(json.dumps({"type": "typing", "data": {"is_typing": True}}))

    await anyio.to_thread.run_sync(_run)

    room_id = str(test_room["id"])
    resp = await async_client.get(
        f"/rooms/{room_id}/history",
        headers=auth_headers,
        params={"limit": 50, "offset": 0},
    )
    assert resp.status_code == 200
    assert resp.json() == []

