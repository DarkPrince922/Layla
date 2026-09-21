"""JWT session tokens carried in an httpOnly cookie (spec §4)."""
from __future__ import annotations

import datetime as dt
from typing import Any

import jwt

from app.config import get_settings

ALGORITHM = "HS256"
COOKIE_NAME = "layla_session"


def create_session_token(user_id: str, extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    now = dt.datetime.now(tz=dt.timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.jwt_ttl_minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_session_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
