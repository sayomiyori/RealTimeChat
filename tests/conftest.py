from __future__ import annotations

import asyncio
import os
from typing import AsyncGenerator, Generator
from urllib.parse import urlparse, urlunparse
from uuid import UUID
from unittest.mock import AsyncMock

import anyio
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.base import Base
from app.models.message import Message
from app.models.room import Room
from app.models.user import User


def _to_test_db_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    db_name = (parsed.path or "").lstrip("/")
    test_db_name = f"{db_name}_test"
    return urlunparse(parsed._replace(path=f"/{test_db_name}"))


# Configure DATABASE_URL BEFORE importing app modules that build engines/sessionmakers.
# `DATABASE_URL` может быть не задан в окружении на хосте, но берётся приложением из `.env`,
# поэтому сначала читаем его из app.core.config.settings, затем делаем reload config.
import importlib

import app.core.config as config_mod

_base_db_url = config_mod.settings.DATABASE_URL
_test_db_url = _to_test_db_url(_base_db_url)
os.environ["DATABASE_URL"] = _test_db_url

# Reload config so that app.core.db builds engine using *_test url.
config_mod = importlib.reload(config_mod)

from app.main import app as fastapi_app  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db import engine as app_engine  # noqa: E402


@pytest.fixture(scope="session")
def app() -> FastAPI:
    return fastapi_app


@pytest.fixture(scope="session")
async def ensure_test_database_exists() -> AsyncGenerator[None, None]:
    """Create the test database if it doesn't exist."""
    import asyncpg

    parsed = urlparse(settings.DATABASE_URL)
    db_name = (parsed.path or "").lstrip("/")

    admin_parsed = parsed._replace(path="/postgres")
    admin_url = urlunparse(admin_parsed)

    conn = await asyncpg.connect(admin_url)
    try:
        try:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
        except asyncpg.exceptions.DuplicateDatabaseError:
            pass
    finally:
        await conn.close()

    yield


@pytest.fixture(scope="session", autouse=True)
async def create_tables(ensure_test_database_exists: None) -> AsyncGenerator[None, None]:
    await ensure_test_database_exists

    async with app_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with app_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def async_client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app, lifespan="on")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(scope="session")
async def test_user(async_client: AsyncClient) -> dict[str, object]:
    payload = {"username": "testuser", "email": "testuser@example.com", "password": "password123"}
    resp = await async_client.post("/auth/register", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
async def auth_headers(async_client: AsyncClient, test_user: dict[str, object]) -> dict[str, str]:
    resp = await async_client.post(
        "/auth/token",
        data={"username": str(test_user["username"]), "password": "password123"},
    )
    assert resp.status_code == 200, resp.text
    access_token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture
async def test_room(async_client: AsyncClient, auth_headers: dict[str, str]) -> dict[str, object]:
    # Room model enforces `name` uniqueness, so we generate a unique name per fixture call.
    resp = await async_client.post(
        "/rooms",
        json={"name": f"test-room-{os.urandom(4).hex()}", "description": "room description"},
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


@pytest.fixture(autouse=True)
def mock_redis(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Mock Redis so tests are isolated from external services."""
    import app.main as main_mod
    import app.routers.chat as chat_mod

    # Lifespan calls.
    monkeypatch.setattr(main_mod, "get_redis", AsyncMock())
    monkeypatch.setattr(main_mod, "close_redis", AsyncMock())

    # WebSocket calls.
    fake_pubsub = type(
        "FakePubSub",
        (),
        {
            "get_message": AsyncMock(return_value=None),
            "unsubscribe": AsyncMock(),
        },
    )()

    monkeypatch.setattr(chat_mod, "publish", AsyncMock())
    monkeypatch.setattr(chat_mod, "subscribe", AsyncMock(return_value=fake_pubsub))

    yield


@pytest.fixture(autouse=True)
def clear_connection_manager() -> Generator[None, None, None]:
    # Avoid cross-test contamination of `online_count`.
    from app.services.connection import manager as connection_manager

    connection_manager._connections.clear()  # noqa: SLF001
    yield

