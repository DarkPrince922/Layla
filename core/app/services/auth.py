"""Auth service: current-user dependency and first-login persona seeding."""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.persona import Persona
from app.models.user import AdminBootstrap, User, Workspace
from app.security.jwt import COOKIE_NAME, decode_session_token
from app.security.passwords import generate_password, hash_password
from app.services.personas_seed import BUILTIN_PERSONAS

logger = logging.getLogger("layla.auth")


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


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Зависимость: доступ только администратору."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Действие доступно только администратору",
        )
    return user


async def ensure_admin_bootstrapped(session: AsyncSession) -> None:
    """Гарантировать наличие администратора при старте (спец. §4).

    - Если админов нет, но пользователи есть → повысить самого раннего (миграция
      существующей установки: владелец получает права админа, пароль прежний).
    - Если пользователей нет вовсе → создать админа со случайным паролем и
      положить одноразовые учётные данные в admin_bootstrap (покажем в UI).
    Идемпотентно: при уже существующем админе не делает ничего.
    """
    admin_count = await session.scalar(
        select(func.count()).select_from(User).where(User.is_admin.is_(True))
    )
    if admin_count:
        return

    earliest = await session.scalar(select(User).order_by(User.created_at).limit(1))
    if earliest is not None:
        earliest.is_admin = True
        await session.commit()
        logger.warning(
            "Bootstrap: администратор не найден — права выданы пользователю %s",
            earliest.email,
        )
        return

    # Свежая установка: создаём админа со случайным паролем.
    email = get_settings().admin_email.lower().strip()
    password = generate_password()
    admin = User(
        email=email,
        pw_hash=hash_password(password),
        display_name="Администратор",
        is_admin=True,
        must_change_password=True,
    )
    session.add(admin)
    # Прежние одноразовые записи (если были) убираем — актуальна только новая.
    for row in (await session.scalars(select(AdminBootstrap))).all():
        await session.delete(row)
    session.add(AdminBootstrap(email=email, password=password))
    await session.commit()
    logger.warning(
        "Bootstrap: создан администратор %s. Пароль показан в окне входа и в этих "
        "логах один раз: %s",
        email,
        password,
    )


async def ensure_user_bootstrapped(session: AsyncSession, user: User) -> None:
    """Seed a default workspace and built-in personas on first login."""
    existing_ws = await session.scalar(
        select(Workspace).where(Workspace.owner_id == user.id).limit(1)
    )
    if existing_ws is None:
        session.add(
            Workspace(
                owner_id=user.id,
                name="По умолчанию",
                projects_dir=get_settings().projects_dir,
            )
        )

    existing_persona = await session.scalar(
        select(Persona).where(Persona.owner_id == user.id).limit(1)
    )
    if existing_persona is None:
        for spec in BUILTIN_PERSONAS:
            session.add(Persona(owner_id=user.id, is_builtin=True, **spec))
    await session.flush()
