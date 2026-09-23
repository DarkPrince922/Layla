"""Provider profile routes (spec §5.1).

Profiles are stored here and (from M1) rendered into LiteLLM config. API keys
are encrypted at rest and never returned in plaintext — only a ``has_secret``
flag and, on demand, a masked form.
"""
from __future__ import annotations

from datetime import UTC, datetime

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


# ---------------------------------------------------------------------------
# Ключи провайдера (ротация, спец. §5.1) и Accounts Lab
# ---------------------------------------------------------------------------
from app.models.enums import KeyStatus  # noqa: E402
from app.models.provider import ProviderKey  # noqa: E402
from app.schemas.provider import (  # noqa: E402
    AccountsHealth,
    KeyStatusUpdate,
    ModelOut,
    ProviderKeyCreate,
    ProviderKeyOut,
)
from app.services import litellm as litellm_svc  # noqa: E402


async def _owned_provider(session: AsyncSession, user: User, provider_id: str) -> Provider:
    provider = await session.get(Provider, provider_id)
    if provider is None or provider.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Провайдер не найден")
    return provider


def _key_out(k: ProviderKey) -> ProviderKeyOut:
    out = ProviderKeyOut.model_validate(k)
    try:
        out.masked = crypto.mask(crypto.decrypt(k.secret_ref))
    except ValueError:
        out.masked = "••••"
    out.status = k.status.value if hasattr(k.status, "value") else str(k.status)
    return out


@router.get("/{provider_id}/keys", response_model=list[ProviderKeyOut])
async def list_keys(
    provider_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ProviderKeyOut]:
    await _owned_provider(session, user, provider_id)
    rows = await session.scalars(
        select(ProviderKey).where(ProviderKey.provider_id == provider_id)
        .order_by(ProviderKey.created_at, ProviderKey.id)  # в этом порядке ключи и используются
    )
    return [_key_out(k) for k in rows]


@router.post("/{provider_id}/keys", response_model=ProviderKeyOut, status_code=201)
async def add_key(
    provider_id: str,
    body: ProviderKeyCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProviderKeyOut:
    await _owned_provider(session, user, provider_id)
    key = ProviderKey(
        provider_id=provider_id,
        label=body.label,
        secret_ref=crypto.encrypt(body.api_key),
        status=KeyStatus.active,
        # Точное время: по нему ключи берутся по порядку (func.now() в SQLite — до секунды).
        created_at=datetime.now(UTC),
    )
    session.add(key)
    await session.flush()
    await audit.record(
        session, actor=user.id, action="provider.key.add", target=provider_id,
        meta={"key_id": key.id},
    )
    await session.commit()
    return _key_out(key)


@router.post("/keys/{key_id}/status", response_model=ProviderKeyOut)
async def set_key_status(
    key_id: str,
    body: KeyStatusUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProviderKeyOut:
    key = await session.get(ProviderKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="Ключ не найден")
    await _owned_provider(session, user, key.provider_id)
    try:
        key.status = KeyStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=422, detail="Недопустимый статус ключа")
    await session.commit()
    return _key_out(key)


@router.delete("/keys/{key_id}", status_code=204)
async def delete_key(
    key_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    key = await session.get(ProviderKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="Ключ не найден")
    await _owned_provider(session, user, key.provider_id)
    await session.delete(key)
    await session.commit()


@router.get("/accounts/health", response_model=AccountsHealth)
async def accounts_health(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AccountsHealth:
    """Сводка для Accounts Lab (спец. §5.1)."""
    providers = list(await session.scalars(select(Provider).where(Provider.owner_id == user.id)))
    provider_ids = [p.id for p in providers]
    keys: list[ProviderKey] = []
    if provider_ids:
        keys = list(
            await session.scalars(
                select(ProviderKey).where(ProviderKey.provider_id.in_(provider_ids))
            )
        )
    quota_limited = sum(
        1 for k in keys if k.status in (KeyStatus.rate_limited, KeyStatus.exhausted)
    )
    return AccountsHealth(
        profiles=len(providers),
        active_models=len(litellm_svc.active_model_names(providers)),
        oauth_accounts=0,  # OAuth-подписки — вторая фаза (спец. §5.1)
        quota_limited=quota_limited,
    )


# ---------------------------------------------------------------------------
# Модели провайдера: загрузка списка и включение/выключение (Settings→Providers)
# ---------------------------------------------------------------------------
from app.schemas.provider import ProviderModelInfo, ProviderModelsUpdate  # noqa: E402
from app.services import provider_client  # noqa: E402


@router.get("/{provider_id}/models", response_model=list[ProviderModelInfo])
async def get_provider_models(
    provider_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ProviderModelInfo]:
    provider = await _owned_provider(session, user, provider_id)
    # Список не загружали — в чатах работает модель по умолчанию; её тоже можно настроить.
    entries = provider.models or ([{"name": provider.default_model, "enabled": True}] if provider.default_model else [])
    return _with_caps(provider, entries)


@router.post("/{provider_id}/fetch-models", response_model=list[ProviderModelInfo])
async def fetch_provider_models(
    provider_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ProviderModelInfo]:
    """Запросить список моделей у провайдера (GET /models) и сохранить его.

    Уже известные модели сохраняют свой флаг enabled; новые добавляются как
    включённые. Так в пикере чата видны только выбранные модели.
    """
    provider = await _owned_provider(session, user, provider_id)
    key = await provider_client.pick_key(session, provider)
    try:
        names = await provider_client.list_models(provider, key)
    except Exception as exc:  # сеть/провайдер
        raise HTTPException(status_code=502, detail=f"Не удалось загрузить модели: {exc}") from exc

    prev = {m["name"]: bool(m.get("enabled", True)) for m in (provider.models or [])}
    merged = [{"name": n, "enabled": prev.get(n, True)} for n in names]
    provider.models = merged
    await session.commit()
    return _with_caps(provider, merged)


@router.put("/{provider_id}/models", response_model=list[ProviderModelInfo])
async def set_provider_models(
    provider_id: str,
    body: ProviderModelsUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ProviderModelInfo]:
    provider = await _owned_provider(session, user, provider_id)
    provider.models = [{"name": m.name, "enabled": m.enabled} for m in body.models]
    caps = dict(provider.model_caps or {})
    for m in body.models:
        entry = {"tools": m.tools} if m.reset else {**(caps.get(m.name) or {}), "tools": m.tools}
        if m.max_output_manual and m.max_output:
            entry.update(max_output=m.max_output, max_output_cap=m.max_output, max_output_manual=True)
        elif entry.get("max_output_manual"):
            # Вернули «Авто»: снимаем ручной потолок, дальше лимит подбирается сам.
            for key in ("max_output", "max_output_cap", "max_output_manual"):
                entry.pop(key, None)
        for key in ("context", "temperature", "reasoning_effort", "reasoning_budget"):
            value = getattr(m, key)
            if value is None:
                entry.pop(key, None)
            else:
                entry[key] = value
        caps[m.name] = entry
    provider.model_caps = caps
    await session.commit()
    return _with_caps(provider, provider.models)


def _with_caps(provider: Provider, entries: list[dict]) -> list[ProviderModelInfo]:
    caps = provider.model_caps or {}
    out = []
    for m in entries:
        c = caps.get(m["name"]) or {}
        out.append(ProviderModelInfo(
            name=m["name"], enabled=bool(m.get("enabled", True)), tools=c.get("tools") is not False,
            max_output=c.get("max_output"), max_output_manual=bool(c.get("max_output_manual")),
            context=c.get("context"), temperature=c.get("temperature"),
            reasoning_effort=c.get("reasoning_effort"), reasoning_budget=c.get("reasoning_budget"),
            dropped=list(c.get("drop") or []),
        ))
    return out
