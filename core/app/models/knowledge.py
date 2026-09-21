"""База знаний / RAG (спец. §5.9).

Документ нарезается на чанки, каждый чанк хранит эмбеддинг. Эмбеддинги хранятся
как JSON-массив float (переносимо: SQLite для тестов, а на Postgres поверх этого
можно включить pgvector-индекс во второй итерации). Поиск — косинусное сходство.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.types import JSONList


class KnowledgeDoc(UUIDPk, Timestamps, Base):
    __tablename__ = "knowledge_docs"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str | None] = mapped_column(String(1024))
    # Привязка к домену/персоне (опционально).
    domain: Mapped[str | None] = mapped_column(String(32))
    persona_id: Mapped[str | None] = mapped_column(String(36))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)


class KnowledgeChunk(UUIDPk, Timestamps, Base):
    __tablename__ = "knowledge_chunks"

    doc_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_docs.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[list] = mapped_column(JSONList, default=list)
    domain: Mapped[str | None] = mapped_column(String(32))
