"""Доступ к git-хостингам: токен пользователя для пуша и пулла (хранится зашифрованным)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk


class GitCredential(UUIDPk, Timestamps, Base):
    __tablename__ = "git_credentials"
    __table_args__ = (UniqueConstraint("owner_id", "host", name="uq_git_credentials_owner_host"),)

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)  # зашифрованный токен
