"""Authentication routes (spec §4): login, logout, me, first-run bootstrap.

Открытой саморегистрации нет: администратор создаётся при первой установке
(см. ensure_admin_bootstrapped), остальных пользователей заводит админ через
/api/admin/users.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.user import AdminBootstrap, User
from app.schemas.auth import (
    BootstrapOut,
    LoginRequest,
    PasswordChange,
    RegisterRequest,
    UserOut,
)
from app.security.jwt import COOKIE_NAME, create_session_token
from app.security.passwords import hash_password, needs_rehash, verify_password
from app.services import audit
from app.services.auth import ensure_user_bootstrapped, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, user_id: str) -> None:
    settings = get_settings()
    token = create_session_token(user_id)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.is_prod,   # HTTPS-only cookie in prod
        samesite="lax",
        max_age=settings.jwt_ttl_minutes * 60,
        path="/",
    )


async def _clear_bootstrap(session: AsyncSession) -> None:
    """Удалить одноразовые учётные данные админа — больше их не показываем."""
    for row in (await session.scalars(select(AdminBootstrap))).all():
        await session.delete(row)


@router.get("/bootstrap", response_model=BootstrapOut)
async def bootstrap(session: AsyncSession = Depends(get_session)) -> BootstrapOut:
    """Одноразовые учётные данные админа для окна первого входа.

    Публичный эндпойнт: данные существуют только до первого входа админа.
    """
    row = await session.scalar(select(AdminBootstrap).limit(1))
    if row is None:
        return BootstrapOut(available=False)
    return BootstrapOut(available=True, email=row.email, password=row.password)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> User:
    """Саморегистрация. Доступна только при LAYLA_ALLOW_OPEN_REGISTRATION=true.

    По умолчанию закрыта — пользователей заводит администратор
    (POST /api/admin/users).
    """
    if not get_settings().allow_open_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Регистрация закрыта. Учётные записи создаёт администратор.",
        )
    email = body.email.lower().strip()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Эта почта уже зарегистрирована")
    user = User(
        email=email,
        pw_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    session.add(user)
    await session.flush()
    await ensure_user_bootstrapped(session, user)
    await audit.record(session, actor=user.id, action="user.register", target=user.email)
    await session.commit()
    _set_session_cookie(response, user.id)
    return user


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> User:
    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None or not verify_password(body.password, user.pw_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверная почта или пароль"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Учётная запись отключена"
        )
    if needs_rehash(user.pw_hash):
        user.pw_hash = hash_password(body.password)
    await ensure_user_bootstrapped(session, user)
    if user.is_admin:
        await _clear_bootstrap(session)
    await audit.record(session, actor=user.id, action="user.login", target=user.email)
    await session.commit()
    _set_session_cookie(response, user.id)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.post("/change-password", response_model=UserOut)
async def change_password(
    body: PasswordChange,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> User:
    if not verify_password(body.current_password, user.pw_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Текущий пароль неверен")
    user.pw_hash = hash_password(body.new_password)
    user.must_change_password = False
    if user.is_admin:
        await _clear_bootstrap(session)
    await audit.record(session, actor=user.id, action="user.password_change", target=user.email)
    await session.commit()
    return user


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
