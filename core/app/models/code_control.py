"""Code-only plan decisions, recovery checkpoints and usage accounting."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.types import JSONList


class CodePlan(UUIDPk, Timestamps, Base):
    __tablename__ = "code_plans"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    workspace_id: Mapped[str | None] = mapped_column(String(36))
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    feedback: Mapped[str] = mapped_column(Text, default="")
    execution_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    mode: Mapped[str | None] = mapped_column(String(16))


class CodeRecovery(Timestamps, Base):
    __tablename__ = "code_recovery"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    workspace_id: Mapped[str | None] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSONList, default=dict)


class CodeMemoryDigest(Timestamps, Base):
    __tablename__ = "code_memory_digests"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)


class CodeUsagePolicy(Timestamps, Base):
    __tablename__ = "code_usage_policies"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    max_tokens: Mapped[int | None] = mapped_column(BigInteger)
    max_cost_microusd: Mapped[int | None] = mapped_column(BigInteger)
    prices: Mapped[dict] = mapped_column(JSONList, default=dict)
    tokens_used: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_used: Mapped[int] = mapped_column(BigInteger, default=0)
    revision: Mapped[int] = mapped_column(default=0)


class CodeUsageCall(UUIDPk, Timestamps, Base):
    __tablename__ = "code_usage_calls"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    provider_id: Mapped[str] = mapped_column(String(36))
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="reserved")
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    charged_tokens: Mapped[int] = mapped_column(BigInteger)
    charged_cost: Mapped[int] = mapped_column(BigInteger)
    prices: Mapped[dict] = mapped_column(JSONList, default=dict)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CodeContextView(Timestamps, Base):
    __tablename__ = "code_context_views"
    turn_id: Mapped[str] = mapped_column(ForeignKey("code_lead_turns.id", ondelete="CASCADE"), primary_key=True)
    next_sequence: Mapped[int] = mapped_column(default=0)
    messages: Mapped[list] = mapped_column(JSONList, default=list)
