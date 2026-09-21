"""Настройки Telegram-бота (спец. §5.8). Токен хранится зашифрованным."""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk


class TelegramConfig(UUIDPk, Timestamps, Base):
    __tablename__ = "telegram_configs"

    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    bot_token_ref: Mapped[str] = mapped_column(Text, nullable=False)  # зашифрован
    default_chat_id: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
