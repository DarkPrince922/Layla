"""Agent runs — orchestration bookkeeping and budgets (spec §5.2)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.enums import AgentRunStatus, Domain
from app.models.types import JSONList


class AgentRun(UUIDPk, Timestamps, Base):
    __tablename__ = "agent_runs"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    domain: Mapped[Domain] = mapped_column(default=Domain.code)
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.id", ondelete="SET NULL")
    )
    status: Mapped[AgentRunStatus] = mapped_column(default=AgentRunStatus.queued)
    trace_ref: Mapped[str | None] = mapped_column(String(200))
    budget_used: Mapped[dict] = mapped_column(JSONList, default=dict)
