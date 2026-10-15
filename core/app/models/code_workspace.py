"""Chat-owned lead worktrees and immutable checkpoint location bindings."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.types import JSONList


class CodeWorkspace(UUIDPk, Timestamps, Base):
    __tablename__ = "code_workspaces"
    __table_args__ = (UniqueConstraint("chat_id", "active_slot", name="uq_code_workspace_active"),)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    branch: Mapped[str] = mapped_column(String(200))
    source_root: Mapped[str] = mapped_column(Text)
    baseline: Mapped[str | None] = mapped_column(String(40))
    active_slot: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="creating")
    payload: Mapped[dict] = mapped_column(JSONList, default=dict)


class CodeWorkspaceCheckpoint(Base):
    __tablename__ = "code_workspace_checkpoints"
    checkpoint_id: Mapped[str] = mapped_column(ForeignKey("checkpoints.id", ondelete="CASCADE"), primary_key=True)
    # Keep the location identity even if a workspace row is removed: a missing
    # workspace must fail rollback, never turn into a main-project checkpoint.
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
