"""Durable Code conversation state, removed together with its conversation."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.types import JSONList


class CodeState(Base):
    __tablename__ = "code_states"

    chat_id: Mapped[str] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONList, default=dict, nullable=False)
