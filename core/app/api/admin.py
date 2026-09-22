"""Управление пользователями администратором (спец. §4).

Только администратор может создавать, отключать и сбрасывать пароли пользователей.
Изоляция данных (чаты, проекты, провайдеры и т.д.) уже обеспечивается owner_id.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.user import User
from app.schemas.auth import (
    AdminUserCreate,
    AdminUserCreated,
    AdminUserOut,
    AdminUserUpdate,
)
from app.security.passwords import generate_password, hash_password
from app.services import audit
from app.services.auth import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


def _out(user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        created_at=user.created_at.isoformat() if user.created_at else None,
    )


async def _active_admin_count(session: AsyncSession, exclude_id: str | None = None) -> int:
    q = select(func.count()).select_from(User).where(
        User.is_admin.is_(True), User.is_active.is_(True)
    )
    if exclude_id:
        q = q.where(User.id != exclude_id)
    return int(await session.scalar(q) or 0)


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_admin),
) -> list[AdminUserOut]:
    users = (await session.scalars(select(User).order_by(User.created_at))).all()
    return [_out(u) for u in users]


@router.post("/users", response_model=AdminUserCreated, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: AdminUserCreate,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> AdminUserCreated:
    email = body.email.lower().strip()
    exists = await session.scalar(select(User).where(User.email == email))
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Эта почта уже зарегистрирована")

    generated = None
    password = body.password
    if not password:
        password = generate_password()
        generated = password

    user = User(
        email=email,
        pw_hash=hash_password(password),
        display_name=body.display_name,
        is_admin=body.is_admin,
        # Рабочее пространство и персоны заводятся при первом входе пользователя.
        must_change_password=True,
    )
    session.add(user)
    await session.flush()
    await audit.record(session, actor=admin.id, action="admin.user_create", target=user.email)
    await session.commit()
    return AdminUserCreated(user=_out(user), generated_password=generated)


@router.patch("/users/{user_id}", response_model=AdminUserCreated)
async def update_user(
    user_id: str,
    body: AdminUserUpdate,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> AdminUserCreated:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    # Нельзя лишить прав/отключить последнего активного администратора.
    losing_admin = user.is_admin and (body.is_admin is False or body.is_active is False)
    if losing_admin and await _active_admin_count(session, exclude_id=user.id) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя отключить или разжаловать единственного администратора",
        )

    if body.display_name is not None:
        user.display_name = body.display_name
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.is_active is not None:
        user.is_active = body.is_active

    generated = None
    if body.reset_password:
        generated = generate_password()
        user.pw_hash = hash_password(generated)
        user.must_change_password = True

    await audit.record(session, actor=admin.id, action="admin.user_update", target=user.email)
    await session.commit()
    return AdminUserCreated(user=_out(user), generated_password=generated)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> None:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нельзя удалить самого себя")
    if user.is_admin and await _active_admin_count(session, exclude_id=user.id) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить единственного администратора",
        )
    await audit.record(session, actor=admin.id, action="admin.user_delete", target=user.email)
    await session.delete(user)
    await session.commit()
