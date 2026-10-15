"""Code-chat teams, durable mailboxes and append-only worker conversations."""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.types import JSONList


class CodeTeam(UUIDPk, Timestamps, Base):
    __tablename__ = "code_teams"
    __table_args__ = (UniqueConstraint("chat_id", "active_slot", name="uq_code_team_active"),)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    active_slot: Mapped[int | None] = mapped_column(Integer, default=1)


class CodeTeamMessage(UUIDPk, Timestamps, Base):
    __tablename__ = "code_team_messages"
    __table_args__ = (UniqueConstraint("team_id", "sequence", name="uq_code_team_message_sequence"),)
    team_id: Mapped[str] = mapped_column(ForeignKey("code_teams.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    sender: Mapped[str] = mapped_column(String(80))
    recipient: Mapped[str] = mapped_column(String(80), index=True)
    summary: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)


class CodeTranscript(UUIDPk, Timestamps, Base):
    __tablename__ = "code_transcripts"
    __table_args__ = (UniqueConstraint("worker_id", "sequence", name="uq_code_transcript_sequence"),)
    worker_id: Mapped[str] = mapped_column(ForeignKey("code_workers.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    message: Mapped[dict] = mapped_column(JSONList)


class CodeTeamRequest(UUIDPk, Timestamps, Base):
    __tablename__ = "code_team_requests"
    team_id: Mapped[str] = mapped_column(ForeignKey("code_teams.id", ondelete="CASCADE"), index=True)
    sender: Mapped[str] = mapped_column(String(80))
    recipient: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    response: Mapped[str] = mapped_column(Text, default="")
