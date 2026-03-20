from __future__ import annotations

# ---------------------------------------------------------------------------
# Rewrite DATABASE_URL to point at a *_test database BEFORE any app import.
# ---------------------------------------------------------------------------
import importlib
import os
from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock
from urllib.parse import urlparse, urlunparse

import anyio
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.core.config as config_mod
from app.models.base import Base

_base_db_url = config_mod.settings.DATABASE_URL
_parsed = urlparse(_base_db_url)
_test_db_name = (_parsed.path or "").lstrip("/") + "_test"
_test_db_url = urlunparse(_parsed._replace(path=f"/{_test_db_name}"))
os.environ["DATABASE_URL"] = _test_db_url
importlib.reload(config_mod)

import app.core.db as db_mod  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402


def _raw_pg_url(sa_url: str) -> str:
    """Strip '+asyncpg' so raw asyncpg connections work."""
    parsed = urlparse(sa_url)
    if parsed.scheme.startswith("postgresql+"):
        parsed = parsed._replace(scheme="postgresql")
    return urlunparse(parsed)


# ---------------------------------------------------------------------------
# Session-scoped: create test DB and tables once
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app() -> FastAPI:
    return fastapi_app


@pytest.fixture(scope="session")
async def _ensure_test_db() -> AsyncGenerator[None, None]:
    import asyncpg

    parsed = urlparse(_raw_pg_url(settings.DATABASE_URL))
    db_name = (parsed.path or "").lstrip("/")
    admin_url = urlunparse(parsed._replace(path="/postgres"))

    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    except asyncpg.exceptions.DuplicateDatabaseError:
        pass
    finally:
        await conn.close()

    yield


@pytest.fixture(scope="session", autouse=True)
async def _create_tables(_ensure_test_db: None) -> AsyncGenerator[None, None]:
    # Use NullPool so the connection is not cached across event loops.
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


# ---------------------------------------------------------------------------
# Per-test: override the app's get_db with a NullPool engine bound to THIS loop
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _override_get_db(app: FastAPI) -> Generator[None, None, None]:
    """
    Sync fixture: create a NullPool engine (no event-loop state at creation)
    and patch both FastAPI DI and db_mod so every event loop — whether from
    pytest-asyncio or from TestClient — gets fresh connections.
    """
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    original_engine = db_mod.engine
    original_maker = db_mod.async_session_maker
    db_mod.engine = engine
    db_mod.async_session_maker = session_maker

    async def _test_get_db() -> AsyncGenerator[AsyncSession, None]:
        session = db_mod.async_session_maker()
        try:
            yield session
        finally:
            # Shield from anyio task cancellation so the asyncpg connection is
            # fully closed before the event loop is torn down.  Without this,
            # the connection object leaks a pending Future from the old loop and
            # the next TestClient (which creates a new loop) hits
            # "Future attached to a different loop".
            with anyio.CancelScope(shield=True):
                await session.close()

    app.dependency_overrides[db_mod.get_db] = _test_get_db

    yield

    app.dependency_overrides.pop(db_mod.get_db, None)
    db_mod.engine = original_engine
    db_mod.async_session_maker = original_maker


@pytest.fixture
async def async_client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(scope="session")
async def test_user(app: FastAPI) -> dict[str, object]:
    """Register a test user once per session (uses its own ephemeral engine)."""
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    original_engine = db_mod.engine
    original_maker = db_mod.async_session_maker
    db_mod.engine = engine
    db_mod.async_session_maker = maker

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            yield session

    app.dependency_overrides[db_mod.get_db] = _get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "username": "testuser",
            "email": "testuser@example.com",
            "password": "password123",
        }
        resp = await client.post("/auth/register", json=payload)
        assert resp.status_code == 201, resp.text

    app.dependency_overrides.pop(db_mod.get_db, None)
    db_mod.engine = original_engine
    db_mod.async_session_maker = original_maker
    await engine.dispose()

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
    resp = await async_client.post(
        "/rooms",
        json={"name": f"test-room-{os.urandom(4).hex()}", "description": "room description"},
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_redis(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    import asyncio

    import app.main as main_mod
    import app.routers.chat as chat_mod

    monkeypatch.setattr(main_mod, "get_redis", AsyncMock())
    monkeypatch.setattr(main_mod, "close_redis", AsyncMock())

    async def _fake_get_message(**kwargs: object) -> None:
        # Must yield control so redis_listener doesn't spin in a tight loop.
        await asyncio.sleep(0.05)
        return None

    fake_pubsub = type(
        "FakePubSub", (), {
            "get_message": _fake_get_message,
            "unsubscribe": AsyncMock(),
        },
    )()
    monkeypatch.setattr(chat_mod, "publish", AsyncMock())
    monkeypatch.setattr(chat_mod, "subscribe", AsyncMock(return_value=fake_pubsub))

    yield


@pytest.fixture(autouse=True)
def clear_connection_manager() -> Generator[None, None, None]:
    from app.services.connection import manager as connection_manager
    connection_manager._connections.clear()
    yield
