"""OSINT cases (spec §5.5). Passive lookups by default."""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.enums import SubjectType
from app.models.types import JSONList


class OsintCase(UUIDPk, Timestamps, Base):
    __tablename__ = "osint_cases"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_type: Mapped[SubjectType] = mapped_column(default=SubjectType.domain)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    artifacts: Mapped[list] = mapped_column(JSONList, default=list)
    sources: Mapped[list] = mapped_column(JSONList, default=list)


class OsintLookup(UUIDPk, Timestamps, Base):
    """One completed passive query, including empty results and failures."""

    __tablename__ = "osint_lookups"

    case_id: Mapped[str] = mapped_column(
        ForeignKey("osint_cases.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(253))
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    artifact_count: Mapped[int] = mapped_column(default=0)
    duplicate_count: Mapped[int] = mapped_column(default=0)


class OsintArtifact(UUIDPk, Timestamps, Base):
    """A source-attributed observation; JSON is data, never executable content."""

    __tablename__ = "osint_artifacts"
    __table_args__ = (UniqueConstraint("case_id", "fingerprint", name="uq_osint_artifact"),)

    case_id: Mapped[str] = mapped_column(
        ForeignKey("osint_cases.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))  # or "manual"
    target: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(2048))
    data: Mapped[dict] = mapped_column(JSONList, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64))
