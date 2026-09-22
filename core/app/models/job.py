"""Фоновые задачи (background jobs).

Долгие операции (генерация Design и т.п.) выполняются в фоне: задача сразу
возвращает Job, а прогресс/шаги/размышление модели пишутся в эту строку, чтобы
их было видно в панели «В работе» независимо от того, какой домен открыт.
"""
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.types import JSONList


class Job(UUIDPk, Timestamps, Base):
    __tablename__ = "jobs"

    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    domain: Mapped[str] = mapped_column(String(32), default="code")
    kind: Mapped[str] = mapped_column(String(64), default="task")
    title: Mapped[str] = mapped_column(String(300), default="")
    # queued | running | done | error | cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list] = mapped_column(JSONList, default=list)
    result: Mapped[dict] = mapped_column(JSONList, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
