"""Chats and messages (spec §5.2)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.enums import Domain
from app.models.types import JSONList


class Chat(UUIDPk, Timestamps, Base):
    __tablename__ = "chats"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    domain: Mapped[Domain] = mapped_column(default=Domain.code)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    persona_id: Mapped[str | None] = mapped_column(ForeignKey("personas.id", ondelete="SET NULL"))
    title: Mapped[str | None] = mapped_column(String(300))
    model: Mapped[str | None] = mapped_column(String(200))
    # Выбранный провайдер: имя модели бывает у нескольких провайдеров.
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    # Корзина: не пусто — чат удалён и через 7 дней будет стёрт окончательно.
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Message(UUIDPk, Timestamps, Base):
    __tablename__ = "messages"

    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # user|assistant|system|tool
    content: Mapped[str] = mapped_column(Text, default="")
    # Attachments / tool calls / branching metadata.
    meta: Mapped[dict] = mapped_column(JSONList, default=dict)
    parent_id: Mapped[str | None] = mapped_column(String(36))  # message branching


class Checkpoint(UUIDPk, Base):
    """Контрольная точка: файл проекта до первого изменения в ходе агента.

    По ним «Откатить к этой точке» возвращает файлы к состоянию до сообщения.
    content = None — файла до хода не было (откат его удалит).
    """

    __tablename__ = "checkpoints"

    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(4096), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
