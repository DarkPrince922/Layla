"""Auth service: current-user dependency and first-login persona seeding."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.persona import Persona
from app.models.user import User, Workspace
from app.security.jwt import COOKIE_NAME, decode_session_token
from app.services.personas_seed import BUILTIN_PERSONAS


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется аутентификация")
    try:
        payload = decode_session_token(token)
    except Exception as exc:  # invalid/expired token
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Недействительная сессия"
        ) from exc
    user = await session.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неизвестный пользователь")
    return user


async def ensure_user_bootstrapped(session: AsyncSession, user: User) -> None:
    """Seed a default workspace and built-in personas on first login."""
    existing_ws = await session.scalar(
        select(Workspace).where(Workspace.owner_id == user.id).limit(1)
    )
    if existing_ws is None:
        session.add(Workspace(owner_id=user.id, name="По умолчанию", projects_dir="/workspace/projects"))

    existing_persona = await session.scalar(
        select(Persona).where(Persona.owner_id == user.id).limit(1)
    )
    if existing_persona is None:
        for spec in BUILTIN_PERSONAS:
            session.add(Persona(owner_id=user.id, is_builtin=True, **spec))
    await session.flush()
