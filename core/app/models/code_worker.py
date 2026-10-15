"""Independent Code workers. Slots enforce at most three active workers per chat."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.types import JSONList


class CodeWorker(UUIDPk, Timestamps, Base):
    __tablename__ = "code_workers"
    __table_args__ = (UniqueConstraint("chat_id", "slot", name="uq_code_worker_slot"),)

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    slot: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(16), default="implement")
    task: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    output: Mapped[str] = mapped_column(Text, default="")
    report: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text)
    events: Mapped[list] = mapped_column(JSONList, default=list)
    payload: Mapped[dict] = mapped_column(JSONList, default=dict)
    changes: Mapped[list] = mapped_column(JSONList, default=list)
    applied: Mapped[dict] = mapped_column(JSONList, default=dict)
    summary: Mapped[dict] = mapped_column(JSONList, default=dict)
