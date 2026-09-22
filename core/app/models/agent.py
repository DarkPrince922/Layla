"""Agent runs, steps and Ultracode config (spec §5.2, §7.8).

Каждый активный шаг агента (команда к цели) проходит scope/venue-гейт и, при
необходимости, HITL-паузу до подтверждения оператором. Здесь — только модель
данных; проверки — в services/venue_gate.py и services/orchestrator.py.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.enums import AgentRunStatus, Domain
from app.models.types import JSONList


class AgentRun(UUIDPk, Timestamps, Base):
    __tablename__ = "agent_runs"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    domain: Mapped[Domain] = mapped_column(default=Domain.pentest)
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.id", ondelete="SET NULL")
    )
    task: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(20), default="interactive")  # interactive|autonomous
    model: Mapped[str | None] = mapped_column(String(200))
    persona_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[AgentRunStatus] = mapped_column(default=AgentRunStatus.queued)
    trace_ref: Mapped[str | None] = mapped_column(String(200))
    budget_used: Mapped[dict] = mapped_column(JSONList, default=dict)


class AgentStep(UUIDPk, Timestamps, Base):
    __tablename__ = "agent_steps"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(40), default="lead")  # explorer|reviewer|implementer|lead
    kind: Mapped[str] = mapped_column(String(30), default="plan")  # plan|analysis|command|triage
    # proposed|awaiting_approval|approved|denied|running|done|blocked|failed
    status: Mapped[str] = mapped_column(String(30), default="proposed")
    requires_hitl: Mapped[bool] = mapped_column(default=False)
    target: Mapped[str | None] = mapped_column(String(500))
    command: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)


class AgentConfig(UUIDPk, Timestamps, Base):
    """Настройки Ultracode на оператора (Settings → Agent)."""

    __tablename__ = "agent_configs"

    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    preset: Mapped[str] = mapped_column(String(20), default="minimal")  # minimal|pulse|constellation
    role_models: Mapped[dict] = mapped_column(JSONList, default=dict)   # {explorer, reviewer, implementer}
    budgets: Mapped[dict] = mapped_column(JSONList, default=dict)       # {tokens_per_turn, cost_usd, subagent_minutes}
    diagnostics: Mapped[dict] = mapped_column(JSONList, default=dict)   # {command, run_after_edits}
    context: Mapped[dict] = mapped_column(JSONList, default=dict)       # {repo_map, ...}
