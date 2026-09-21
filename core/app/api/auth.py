"""Authentication routes (spec §4): register, login, logout, me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, UserOut
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


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> User:
    existing = await session.scalar(select(User).where(User.email == body.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = User(
        email=body.email,
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
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    if needs_rehash(user.pw_hash):
        user.pw_hash = hash_password(body.password)
    await ensure_user_bootstrapped(session, user)
    await audit.record(session, actor=user.id, action="user.login", target=user.email)
    await session.commit()
    _set_session_cookie(response, user.id)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
