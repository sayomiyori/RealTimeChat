import asyncio
import logging
from datetime import datetime, timedelta, timezone
import uuid
from typing import Any

from fastapi import HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(pwd_context.hash, password)


async def verify_password(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(pwd_context.verify, password, password_hash)


async def create_access_token(data: dict[str, Any]) -> str:
    """Create JWT access token (HS256) with expiry from settings."""
    expire_at = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = dict(data)
    # Keep compatibility with common JWT patterns ("sub") and our WS router.
    if "user_id" in payload and "sub" not in payload:
        payload["sub"] = payload["user_id"]
    payload["exp"] = int(expire_at.timestamp())

    secret = settings.SECRET_KEY.get_secret_value()
    return await asyncio.to_thread(jwt.encode, payload, secret, "HS256")


async def decode_access_token(token: str) -> dict[str, Any]:
    """Decode JWT and return its payload; raises jose errors on invalid token."""
    secret = settings.SECRET_KEY.get_secret_value()
    return await asyncio.to_thread(jwt.decode, token, secret, algorithms=["HS256"])


async def get_current_user(token: str, db: AsyncSession) -> User:
    try:
        payload = await decode_access_token(token)
        user_id_value = payload.get("user_id") or payload.get("sub")
        if user_id_value is None:
            raise ValueError("Missing token user_id")
        user_id = uuid.UUID(str(user_id_value))
    except Exception as exc:  # noqa: BLE001
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

async def get_current_user_ws(token: str, db: AsyncSession) -> User | None:
    """Same as get_current_user, but returns None instead of raising HTTPException."""
    try:
        return await get_current_user(token=token, db=db)
    except HTTPException:
        return None


__all__ = [
    "oauth2_scheme",
    "hash_password",
    "verify_password",
    "create_access_token",
    "decode_access_token",
    "get_current_user",
    "get_current_user_ws",
]

