"""M2: таблицы knowledge_docs, knowledge_chunks, telegram_configs.

Идемпотентно (checkfirst): на свежей БД baseline (0001) уже создаёт все таблицы
из метаданных, поэтому здесь ничего не произойдёт; на БД, развёрнутой до M2,
добавятся только новые таблицы.

Revision ID: 0002_m2
Revises: 0001_baseline
Create Date: 2026-09-21
"""
from __future__ import annotations

from alembic import op

from app.db import Base
import app.models  # noqa: F401

revision = "0002_m2"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

_TABLES = ("knowledge_docs", "knowledge_chunks", "telegram_configs")


def upgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in _TABLES]
    Base.metadata.create_all(bind=bind, tables=tables, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in _TABLES]
    Base.metadata.drop_all(bind=bind, tables=tables, checkfirst=True)
