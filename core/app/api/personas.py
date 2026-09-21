"""Persona listing (spec §5.3). Built-ins are seeded on first login."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.persona import Persona
from app.models.user import User
from app.schemas.persona import PersonaOut
from app.services.auth import get_current_user

router = APIRouter(prefix="/personas", tags=["personas"])


@router.get("", response_model=list[PersonaOut])
async def list_personas(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Persona]:
    rows = await session.scalars(
        select(Persona).where(Persona.owner_id == user.id).order_by(Persona.is_builtin.desc())
    )
    return list(rows)
