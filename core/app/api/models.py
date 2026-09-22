"""Список моделей для пикера (спец. §5.2).

Показываются только ВКЛЮЧЁННЫЕ модели активных провайдеров (управление —
Settings→Providers: загрузка списка + переключение). Если список моделей у
провайдера не заполнен, используется его модель по умолчанию.
"""
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


def enabled_models(provider: Provider) -> list[str]:
    """Включённые модели провайдера (или его default_model как фолбэк)."""
    if provider.models:
        return [m["name"] for m in provider.models if m.get("enabled", True)]
    return [provider.default_model or provider.name]


@router.get("", response_model=list[ModelOut])
async def list_models(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ModelOut]:
    providers = list(
        await session.scalars(
            select(Provider).where(
                Provider.owner_id == user.id,
                Provider.enabled == True,  # noqa: E712
                Provider.active == True,  # noqa: E712
            )
        )
    )
    out: list[ModelOut] = []
    for p in providers:
        for name in enabled_models(p):
            out.append(ModelOut(name=name, provider=p.name, provider_id=p.id))
    return out
