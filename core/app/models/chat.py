"""Chats and messages (spec §5.2)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
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
    persona_id: Mapped[str | None] = mapped_column(ForeignKey("personas.id", ondelete="SET NULL"))
    title: Mapped[str | None] = mapped_column(String(300))
    model: Mapped[str | None] = mapped_column(String(200))


class Message(UUIDPk, Timestamps, Base):
    __tablename__ = "messages"

    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # user|assistant|system|tool
    content: Mapped[str] = mapped_column(Text, default="")
    # Attachments / tool calls / branching metadata.
    meta: Mapped[dict] = mapped_column(JSONList, default=dict)
    parent_id: Mapped[str | None] = mapped_column(String(36))  # message branching
