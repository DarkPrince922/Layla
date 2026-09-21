"""MCP servers and intelligence-API keys (spec §5.8)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.enums import IntelProvider, McpTransport
from app.models.types import JSONList


class McpServer(UUIDPk, Timestamps, Base):
    __tablename__ = "mcp_servers"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    transport: Mapped[McpTransport] = mapped_column(default=McpTransport.stdio)
    command: Mapped[str | None] = mapped_column(Text)  # for stdio transport
    url: Mapped[str | None] = mapped_column(String(1024))  # for http transport
    enabled: Mapped[bool] = mapped_column(default=True)
    # Persona ids allowed to use this server.
    personas: Mapped[list] = mapped_column(JSONList, default=list)
    env: Mapped[dict] = mapped_column(JSONList, default=dict)
    env_secret_ref: Mapped[str | None] = mapped_column(Text)


class IntelKey(UUIDPk, Timestamps, Base):
    """Encrypted API key for a passive intelligence provider (spec §5.8)."""

    __tablename__ = "intel_keys"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[IntelProvider] = mapped_column(nullable=False)
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted
