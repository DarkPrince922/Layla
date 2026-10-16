"""Durable Code prompts and idempotent scheduled job admission."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk


class CodeSchedule(UUIDPk, Timestamps, Base):
    __tablename__ = "code_schedules"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # Retain a removed copy's identity; removal must never become the source tree.
    workspace_id: Mapped[str | None] = mapped_column(String(36))
    prompt: Mapped[str] = mapped_column(Text)
    cron: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    recurring: Mapped[bool] = mapped_column(default=False)
    mode: Mapped[str] = mapped_column(String(16), default="confirm")
    enabled: Mapped[bool] = mapped_column(default=True)
    next_run: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    last_error: Mapped[str | None] = mapped_column(String(500))
