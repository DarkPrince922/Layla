"""Design artifacts (spec §5.6). Rendered in a sandboxed iframe."""
from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.enums import DesignStack
from app.models.types import JSONList


class Design(UUIDPk, Timestamps, Base):
    __tablename__ = "designs"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    brief: Mapped[dict] = mapped_column(JSONList, default=dict)
    stack: Mapped[DesignStack] = mapped_column(default=DesignStack.html)
    files: Mapped[list] = mapped_column(JSONList, default=list)
    design_system_ref: Mapped[str | None] = mapped_column(String(200))
    # Макет, отданный в разработку, живёт в обычном проекте домена «Код»:
    # ссылка делает передачу идемпотентной — повторный клик открывает тот же проект.
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
