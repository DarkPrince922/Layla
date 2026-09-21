"""Список доступных моделей для пикера (спец. §5.2).

По умолчанию отдаёт модель по умолчанию каждого активного профиля. С
``refresh=true`` дополнительно запрашивает у провайдеров их список моделей
(GET /models) и кэширует его на короткое время, чтобы пикер показывал реальные
модели, а не только вписанную вручную.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.provider import Provider
from app.models.user import User
from app.schemas.provider import ModelOut
from app.services import provider_client
from app.services.auth import get_current_user

router = APIRouter(prefix="/models", tags=["models"])

# Простой in-memory кэш: provider_id -> (expires_at, [model_names])
_CACHE: dict[str, tuple[float, list[str]]] = {}
_TTL = 300.0


async def _fetch_models(session: AsyncSession, provider: Provider) -> list[str]:
    now = time.monotonic()
    cached = _CACHE.get(provider.id)
    if cached and cached[0] > now:
        return cached[1]
    try:
        key = await provider_client.pick_key(session, provider)
        names = await provider_client.list_models(provider, key)
    except Exception:
        names = []
    if names:
        _CACHE[provider.id] = (now + _TTL, names)
    return names


@router.get("", response_model=list[ModelOut])
async def list_models(
    refresh: bool = Query(default=False),
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
        names: list[str] = []
        if refresh or p.id in _CACHE:
            names = await _fetch_models(session, p)
        if names:
            for n in names:
                out.append(ModelOut(name=n, provider=p.name, provider_id=p.id))
        else:
            # Фолбэк: модель по умолчанию профиля.
            out.append(
                ModelOut(name=p.default_model or p.name, provider=p.name, provider_id=p.id)
            )
    return out
