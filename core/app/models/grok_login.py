"""Owner-bound, encrypted pending OAuth device authorizations."""
from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPk


class GrokLogin(UUIDPk, Base):
    __tablename__ = "grok_logins"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    next_poll: Mapped[float] = mapped_column(Float, nullable=False)
    interval: Mapped[float] = mapped_column(Float, nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(36))
