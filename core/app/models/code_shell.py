"""Terminal file changes and rollback history, separate from the global job feed."""
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.types import JSONList


class CodeShellRun(UUIDPk, Timestamps, Base):
    __tablename__ = "code_shell_runs"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSONList, default=dict)
