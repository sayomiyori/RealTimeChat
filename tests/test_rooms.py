from __future__ import annotations

from http import HTTPStatus
from typing import Any

from httpx import AsyncClient


async def test_create_room(async_client: AsyncClient, auth_headers: dict[str, str]) -> None:
    resp = await async_client.post(
        "/rooms",
        json={"name": "room-create", "description": "desc"},
        headers=auth_headers,
    )
    assert resp.status_code in (HTTPStatus.OK, HTTPStatus.CREATED)
    data = resp.json()
    assert data["name"] == "room-create"
    assert data["online_count"] == 0


async def test_get_rooms_list(async_client: AsyncClient, auth_headers: dict[str, str], test_room: dict[str, Any]) -> None:
    resp = await async_client.get("/rooms", headers=auth_headers)
    assert resp.status_code == HTTPStatus.OK
    rooms = resp.json()
    assert any(str(room["id"]) == str(test_room["id"]) for room in rooms)


async def test_get_room_history_empty(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    test_room: dict[str, Any],
) -> None:
    room_id = str(test_room["id"])
    resp = await async_client.get(f"/rooms/{room_id}/history", headers=auth_headers, params={"limit": 50, "offset": 0})
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == []


async def test_create_room_duplicate(async_client: AsyncClient, auth_headers: dict[str, str]) -> None:
    first = await async_client.post(
        "/rooms",
        json={"name": "duplicate-room", "description": "desc"},
        headers=auth_headers,
    )
    assert first.status_code in (HTTPStatus.OK, HTTPStatus.CREATED), first.text

    second = await async_client.post(
        "/rooms",
        json={"name": "duplicate-room", "description": "desc2"},
        headers=auth_headers,
    )
    assert second.status_code == HTTPStatus.CONFLICT, second.text

