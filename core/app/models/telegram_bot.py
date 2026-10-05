"""Persisted bot controls and a retryable notification outbox."""
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base
from app.models.base import UUIDPk
from app.models.types import JSONList


class TelegramBotState(UUIDPk, Base):
    __tablename__ = "telegram_bot_states"
    config_id: Mapped[str] = mapped_column(ForeignKey("telegram_configs.id", ondelete="CASCADE"), unique=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    control_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    controller_user_id: Mapped[str | None] = mapped_column(String(64))
    controller_chat_id: Mapped[str | None] = mapped_column(String(64))
    bot_username: Mapped[str | None] = mapped_column(String(64))
    notify_done: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_error: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_cancelled: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    public_url: Mapped[str | None] = mapped_column(String(1024))
    offset: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[dict] = mapped_column(JSONList, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text)


class TelegramNotification(UUIDPk, Base):
    __tablename__ = "telegram_notifications"
    __table_args__ = (UniqueConstraint("config_id", "event_key", name="uq_telegram_event"),)
    config_id: Mapped[str] = mapped_column(ForeignKey("telegram_configs.id", ondelete="CASCADE"), index=True)
    event_key: Mapped[str] = mapped_column(String(250))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    chat_id: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[float] = mapped_column(Float, default=0)
    sent_chunks: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float)
    last_error: Mapped[str | None] = mapped_column(Text)
