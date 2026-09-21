"""LLM provider profiles and rotating keys (spec §5.1).

Profiles are translated into LiteLLM config; the chat backend only ever talks
to LiteLLM. Secrets live in ``secret_ref`` (encrypted), never in plain columns.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.enums import KeyStatus, ProviderKind


class Provider(UUIDPk, Timestamps, Base):
    __tablename__ = "providers"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[ProviderKind] = mapped_column(default=ProviderKind.openai_compatible)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1024))
    default_model: Mapped[str | None] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(default=True)
    active: Mapped[bool] = mapped_column(default=False)
    sort_order: Mapped[int] = mapped_column(default=0)
    # Optional single-key convenience; multi-key rotation uses ProviderKey rows.
    secret_ref: Mapped[str | None] = mapped_column(Text)


class ProviderKey(UUIDPk, Timestamps, Base):
    """One of several rotating keys/accounts for a provider (spec §5.1)."""

    __tablename__ = "provider_keys"

    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str | None] = mapped_column(String(120))
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted
    status: Mapped[KeyStatus] = mapped_column(default=KeyStatus.active)
