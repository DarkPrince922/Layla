"""Explicit per-engagement active command permission."""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017_offensive_policy"
down_revision = "0016_server_host_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "engagements" not in set(inspector.get_table_names()):
        return
    if "offensive_enabled" not in {c["name"] for c in inspector.get_columns("engagements")}:
        with op.batch_alter_table("engagements") as batch:
            batch.add_column(sa.Column("offensive_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("engagements") as batch:
        batch.drop_column("offensive_enabled")
