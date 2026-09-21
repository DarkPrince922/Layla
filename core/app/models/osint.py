"""OSINT cases (spec §5.5). Passive lookups by default."""
from __future__ import annotations

from sqlalchemy import ForeignKey, String
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
