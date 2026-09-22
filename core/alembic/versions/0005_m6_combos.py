"""M6: таблица combos (Combos router).

Идемпотентно (checkfirst): на свежей БД baseline уже создаёт таблицу из
метаданных; на развёрнутой ранее — создаётся здесь.

Revision ID: 0005_m6
Revises: 0004_m5
Create Date: 2026-09-22
"""
from __future__ import annotations

from alembic import op

from app.db import Base
import app.models  # noqa: F401

revision = "0005_m6"
down_revision = "0004_m5"
branch_labels = None
depends_on = None

_TABLES = ("combos",)


def upgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[t] for t in _TABLES]
    Base.metadata.create_all(bind=bind, tables=tables, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[t] for t in _TABLES]
    Base.metadata.drop_all(bind=bind, tables=tables, checkfirst=True)
