"""M5: таблицы agent_steps, agent_configs и новые колонки agent_runs.

Идемпотентно: на свежей БД baseline (0001) уже создаёт всё из метаданных,
поэтому add_column пропускается, если колонка есть; новые таблицы создаются
с checkfirst. На БД, развёрнутой до M5, добавляется недостающее.

Revision ID: 0004_m5
Revises: 0003_m3
Create Date: 2026-09-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import Base
import app.models  # noqa: F401

revision = "0004_m5"
down_revision = "0003_m3"
branch_labels = None
depends_on = None

_NEW_TABLES = ("agent_steps", "agent_configs")
_AGENT_RUN_COLS = {
    "task": sa.Text(),
    "mode": sa.String(20),
    "model": sa.String(200),
    "persona_id": sa.String(36),
}


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    # Новые колонки agent_runs (только если ещё нет).
    for name, coltype in _AGENT_RUN_COLS.items():
        if not _has_column(bind, "agent_runs", name):
            op.add_column("agent_runs", sa.Column(name, coltype, nullable=True))
    # Новые таблицы.
    tables = [Base.metadata.tables[t] for t in _NEW_TABLES]
    Base.metadata.create_all(bind=bind, tables=tables, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[t] for t in _NEW_TABLES]
    Base.metadata.drop_all(bind=bind, tables=tables, checkfirst=True)
    for name in _AGENT_RUN_COLS:
        if _has_column(bind, "agent_runs", name):
            op.drop_column("agent_runs", name)
