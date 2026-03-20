from __future__ import annotations

from http import HTTPStatus

from httpx import AsyncClient


async def test_register_success(async_client: AsyncClient) -> None:
    resp = await async_client.post(
        "/auth/register",
        json={"username": "alice", "email": "alice@example.com", "password": "password123"},
    )
    assert resp.status_code == HTTPStatus.CREATED
    data = resp.json()
    assert data["username"] == "alice"
    assert data["email"] == "alice@example.com"
    assert "id" in data


async def test_register_duplicate_username(
    async_client: AsyncClient,
    test_user: dict[str, object],
) -> None:
    resp = await async_client.post(
        "/auth/register",
        json={
            "username": str(test_user["username"]),
            "email": "other@example.com",
            "password": "password123",
        },
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST


async def test_login_success(async_client: AsyncClient, test_user: dict[str, object]) -> None:
    resp = await async_client.post(
        "/auth/token",
        data={"username": str(test_user["username"]), "password": "password123"},
    )
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


async def test_login_wrong_password(
    async_client: AsyncClient,
    test_user: dict[str, object],
) -> None:
    resp = await async_client.post(
        "/auth/token",
        data={"username": str(test_user["username"]), "password": "wrong_password"},
    )
    assert resp.status_code == HTTPStatus.UNAUTHORIZED

