"""Combos router (спец. §5.1): наборы моделей с per-model lockout."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.combo import Combo
from app.models.user import User
from app.schemas.combo import ComboCreate, ComboOut, ComboUpdate, RouteResult
from app.services import audit
from app.services.auth import get_current_user
from app.services.combos import ModelRouter

router = APIRouter(prefix="/combos", tags=["combos"])


async def _owned(session: AsyncSession, user: User, cid: str) -> Combo:
    c = await session.get(Combo, cid)
    if c is None or c.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Combo не найден")
    return c


@router.get("", response_model=list[ComboOut])
async def list_combos(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Combo]:
    rows = await session.scalars(select(Combo).where(Combo.owner_id == user.id))
    return list(rows)


@router.post("", response_model=ComboOut, status_code=201)
async def create_combo(
    body: ComboCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Combo:
    c = Combo(
        owner_id=user.id, name=body.name, models=body.models,
        enabled=body.enabled, cooldown_seconds=body.cooldown_seconds,
    )
    session.add(c)
    await audit.record(session, actor=user.id, action="combo.create", target=body.name)
    await session.commit()
    return c


@router.patch("/{cid}", response_model=ComboOut)
async def update_combo(
    cid: str,
    body: ComboUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Combo:
    c = await _owned(session, user, cid)
    for f, v in body.model_dump(exclude_unset=True).items():
        setattr(c, f, v)
    await session.commit()
    return c


@router.delete("/{cid}", status_code=204)
async def delete_combo(
    cid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    c = await _owned(session, user, cid)
    await session.delete(c)
    await session.commit()


@router.get("/{cid}/route", response_model=RouteResult)
async def route(
    cid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RouteResult:
    """Вернуть модель, выбранную роутером combo (первую здоровую по приоритету)."""
    c = await _owned(session, user, cid)
    router_ = ModelRouter(c.models or [], cooldown_seconds=c.cooldown_seconds)
    return RouteResult(combo_id=cid, model=router_.pick())
