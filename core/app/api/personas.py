"""Роли (персоны, спец. §5.3): встроенные заводятся при первом входе, свои — в настройках."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.enums import PersonaKind
from app.models.persona import Persona
from app.models.user import User
from app.schemas.persona import FILE_TOOLS, PersonaCreate, PersonaOut, PersonaUpdate
from app.services import audit
from app.services.auth import get_current_user
from app.services.personas_seed import BUILTIN_PERSONAS

router = APIRouter(prefix="/personas", tags=["personas"])


def _file_tools(requested: list[str]) -> list[str]:
    return [t for t in FILE_TOOLS if t in requested]


async def _owned(session: AsyncSession, user: User, persona_id: str) -> Persona:
    persona = await session.get(Persona, persona_id)
    if persona is None or persona.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Роль не найдена")
    return persona


@router.get("", response_model=list[PersonaOut])
async def list_personas(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Persona]:
    rows = await session.scalars(
        select(Persona).where(Persona.owner_id == user.id)
        .order_by(Persona.is_builtin.desc(), Persona.created_at)
    )
    return list(rows)


@router.post("", response_model=PersonaOut, status_code=201)
async def create_persona(
    body: PersonaCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Persona:
    persona = Persona(
        owner_id=user.id, name=body.name.strip(), kind=PersonaKind.custom, is_builtin=False,
        instructions=body.instructions, icon=body.icon, color=body.color,
        allowed_tools=_file_tools(body.allowed_tools),
        default_model=body.default_model, default_mode=body.default_mode,
        hitl_required=True,
    )
    session.add(persona)
    await audit.record(session, actor=user.id, action="persona.create", target=persona.name)
    await session.commit()
    return persona


@router.patch("/{persona_id}", response_model=PersonaOut)
async def update_persona(
    persona_id: str,
    body: PersonaUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Persona:
    persona = await _owned(session, user, persona_id)
    given = body.model_fields_set
    if "name" in given and body.name:
        persona.name = body.name.strip()
    for field in ("instructions", "icon", "color", "default_model", "default_mode"):
        if field in given:
            setattr(persona, field, getattr(body, field))
    # У встроенных ролей набор прав фиксирован; у своих меняется только доступ к файлам.
    if "allowed_tools" in given and body.allowed_tools is not None and not persona.is_builtin:
        others = [t for t in (persona.allowed_tools or []) if t not in FILE_TOOLS]
        persona.allowed_tools = others + _file_tools(body.allowed_tools)
    await audit.record(session, actor=user.id, action="persona.update", target=persona.id)
    await session.commit()
    return persona


@router.post("/{persona_id}/reset", response_model=PersonaOut)
async def reset_persona(
    persona_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Persona:
    """Вернуть встроенной роли исходные имя, промт, оформление и права."""
    persona = await _owned(session, user, persona_id)
    spec = next((s for s in BUILTIN_PERSONAS if s["kind"] == persona.kind), None)
    if not persona.is_builtin or spec is None:
        raise HTTPException(status_code=400, detail="Сбросить можно только встроенную роль")
    for field, value in spec.items():
        setattr(persona, field, list(value) if isinstance(value, list) else value)
    persona.default_model = None
    persona.default_mode = None
    await audit.record(session, actor=user.id, action="persona.reset", target=persona.id)
    await session.commit()
    return persona


@router.delete("/{persona_id}", status_code=204)
async def delete_persona(
    persona_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    persona = await _owned(session, user, persona_id)
    if persona.is_builtin:
        raise HTTPException(status_code=400, detail="Встроенную роль удалить нельзя — её можно сбросить")
    await session.delete(persona)  # чаты с этой ролью останутся, роль в них сбросится
    await audit.record(session, actor=user.id, action="persona.delete", target=persona_id)
    await session.commit()
