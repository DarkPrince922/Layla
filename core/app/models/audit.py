"""Audit log — mandatory record of sensitive actions (spec §7.7)."""
from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.types import JSONList


class AuditLog(UUIDPk, Timestamps, Base):
    __tablename__ = "audit_logs"

    actor: Mapped[str | None] = mapped_column(String(120))       # user id / "agent"
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    target: Mapped[str | None] = mapped_column(String(500))
    meta: Mapped[dict] = mapped_column(JSONList, default=dict)
    note: Mapped[str | None] = mapped_column(Text)
