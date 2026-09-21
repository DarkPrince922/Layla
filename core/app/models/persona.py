"""Personas — switch the AI's role, tool access and boundaries (spec §5.3)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.enums import PersonaKind
from app.models.types import JSONList


class Persona(UUIDPk, Timestamps, Base):
    __tablename__ = "personas"

    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[PersonaKind] = mapped_column(default=PersonaKind.custom)
    icon: Mapped[str | None] = mapped_column(String(64))
    color: Mapped[str | None] = mapped_column(String(32))
    instructions: Mapped[str | None] = mapped_column(Text)
    allowed_tools: Mapped[list] = mapped_column(JSONList, default=list)
    # Venue policy governs shell/network access this persona may request.
    venue_policy: Mapped[dict] = mapped_column(JSONList, default=dict)
    is_builtin: Mapped[bool] = mapped_column(default=False)
    # HITL: pause on dangerous steps for this persona (spec §7.8).
    hitl_required: Mapped[bool] = mapped_column(default=True)
