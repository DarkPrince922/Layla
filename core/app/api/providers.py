"""Provider profile routes (spec §5.1).

Profiles are stored here and (from M1) rendered into LiteLLM config. API keys
are encrypted at rest and never returned in plaintext — only a ``has_secret``
flag and, on demand, a masked form.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.provider import Provider
from app.models.user import User
from app.schemas.provider import ProviderCreate, ProviderOut
from app.security import crypto
from app.services import audit
from app.services.auth import get_current_user

router = APIRouter(prefix="/providers", tags=["providers"])


def _to_out(p: Provider) -> ProviderOut:
    out = ProviderOut.model_validate(p)
    out.has_secret = bool(p.secret_ref)
    return out


@router.get("", response_model=list[ProviderOut])
async def list_providers(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ProviderOut]:
    rows = await session.scalars(
        select(Provider).where(Provider.owner_id == user.id).order_by(Provider.sort_order)
    )
    return [_to_out(p) for p in rows]


@router.post("", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProviderOut:
    provider = Provider(
        owner_id=user.id,
        name=body.name,
        kind=body.kind,
        base_url=body.base_url,
        default_model=body.default_model,
        enabled=body.enabled,
        active=body.active,
        secret_ref=crypto.encrypt(body.api_key) if body.api_key else None,
    )
    session.add(provider)
    await session.flush()
    await audit.record(
        session, actor=user.id, action="provider.create", target=provider.name,
        meta={"kind": provider.kind.value},
    )
    await session.commit()
    return _to_out(provider)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    provider = await session.get(Provider, provider_id)
    if provider is None or provider.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Провайдер не найден")
    await audit.record(session, actor=user.id, action="provider.delete", target=provider.name)
    await session.delete(provider)
    await session.commit()
