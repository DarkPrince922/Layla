"""The Code lead's protocol journal, separate from its visible chat summaries."""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.types import JSONList


class CodeLeadTurn(Timestamps, Base):
    __tablename__ = "code_lead_turns"
    id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    input_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="running")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    provider_id: Mapped[str | None] = mapped_column(String(36))
    model: Mapped[str | None] = mapped_column(String(200))


class CodeLeadEntry(UUIDPk, Timestamps, Base):
    __tablename__ = "code_lead_entries"
    __table_args__ = (UniqueConstraint("turn_id", "sequence", name="uq_code_lead_entry_sequence"),)
    turn_id: Mapped[str] = mapped_column(ForeignKey("code_lead_turns.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    message: Mapped[dict] = mapped_column(JSONList)


class CodeSessionArchive(UUIDPk, Timestamps, Base):
    __tablename__ = "code_session_archives"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), unique=True)
    entries: Mapped[list] = mapped_column(JSONList, default=list)
