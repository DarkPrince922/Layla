"""Combo — набор моделей-кандидатов с маршрутизацией (спец. §5.1)."""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.types import JSONList


class Combo(UUIDPk, Timestamps, Base):
    __tablename__ = "combos"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    models: Mapped[list] = mapped_column(JSONList, default=list)  # имена моделей по приоритету
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cooldown_seconds: Mapped[int] = mapped_column(default=60)
