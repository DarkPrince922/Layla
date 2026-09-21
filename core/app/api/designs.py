"""Домен Design: генерация и хранение артефактов (спец. §5.6)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.design import Design
from app.models.provider import Provider
from app.models.user import User
from app.schemas.design import DesignCreate, DesignOut
from app.services import audit, design_gen
from app.services.auth import get_current_user
from app.services.litellm import LiteLLMClient, active_model_names

router = APIRouter(prefix="/designs", tags=["design"])


async def _pick_model(session: AsyncSession, user: User, requested: str | None) -> str:
    if requested:
        return requested
    providers = list(await session.scalars(select(Provider).where(Provider.owner_id == user.id)))
    names = active_model_names(providers)
    if not names:
        raise HTTPException(status_code=400, detail="Нет активной модели. Настройте провайдера.")
    return names[0]


@router.get("", response_model=list[DesignOut])
async def list_designs(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Design]:
    rows = await session.scalars(
        select(Design).where(Design.owner_id == user.id).order_by(Design.created_at.desc())
    )
    return list(rows)


@router.get("/{design_id}", response_model=DesignOut)
async def get_design(
    design_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Design:
    design = await session.get(Design, design_id)
    if design is None or design.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Дизайн не найден")
    return design


@router.post("", response_model=DesignOut, status_code=201)
async def create_design(
    body: DesignCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Design:
    """Сгенерировать артефакт по брифу и сохранить его."""
    model = await _pick_model(session, user, body.model)
    messages = design_gen.build_prompt(body.brief.model_dump(), body.stack.value)

    client = LiteLLMClient()
    try:
        raw = await client.complete(model, messages)
    except Exception as exc:  # LiteLLM недоступен и т.п.
        raise HTTPException(status_code=502, detail=f"Ошибка генерации: {exc}") from exc

    html = design_gen.extract_html(raw)
    files = design_gen.to_files(html)

    design = Design(
        owner_id=user.id,
        stack=body.stack,
        brief=body.brief.model_dump(),
        files=files,
    )
    session.add(design)
    await audit.record(session, actor=user.id, action="design.generate", target=body.stack.value)
    await session.commit()
    return design


@router.delete("/{design_id}", status_code=204)
async def delete_design(
    design_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    design = await session.get(Design, design_id)
    if design is None or design.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Дизайн не найден")
    await session.delete(design)
    await session.commit()
