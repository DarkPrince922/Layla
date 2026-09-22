"""M6+: колонка providers.models (кэш моделей провайдера с флагом enabled).

Идемпотентно: add_column только если колонки ещё нет.

Revision ID: 0006_provmodels
Revises: 0005_m6
Create Date: 2026-09-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_provmodels"
down_revision = "0005_m6"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "providers", "models"):
        op.add_column("providers", sa.Column("models", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "providers", "models"):
        op.drop_column("providers", "models")
