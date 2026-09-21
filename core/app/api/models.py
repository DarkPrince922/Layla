"""Список доступных моделей для пикера (спец. §5.2) — из активных профилей."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.provider import Provider
from app.models.user import User
from app.schemas.provider import ModelOut
from app.services.auth import get_current_user

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[ModelOut])
async def list_models(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ModelOut]:
    providers = list(
        await session.scalars(
            select(Provider).where(
                Provider.owner_id == user.id, Provider.enabled == True, Provider.active == True  # noqa: E712
            )
        )
    )
    return [
        ModelOut(name=p.default_model or p.name, provider=p.name, provider_id=p.id)
        for p in providers
    ]
