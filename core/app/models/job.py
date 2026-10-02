"""Фоновые задачи (background jobs).

Долгие операции (генерация Design и т.п.) выполняются в фоне: задача сразу
возвращает Job, а прогресс/шаги/размышление модели пишутся в эту строку, чтобы
их было видно в панели «В работе» независимо от того, какой домен открыт.
"""
from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Float, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.types import JSONList


class Job(UUIDPk, Timestamps, Base):
    __tablename__ = "jobs"
    __mapper_args__: ClassVar[dict] = {"eager_defaults": True}

    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[str | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"), index=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        Index("uq_jobs_active_chat", "chat_id", unique=True,
              postgresql_where=text("chat_id IS NOT NULL AND status IN ('queued', 'running')"),
              sqlite_where=text("chat_id IS NOT NULL AND status IN ('queued', 'running')")),
        Index("uq_jobs_chat_request", "chat_id", "request_id", unique=True),
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
