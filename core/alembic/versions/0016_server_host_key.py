"""Закреплённый ключ хоста attack box (TOFU-пиннинг против MITM)."""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016_server_host_key"
down_revision = "0015_server_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "servers" not in set(inspector.get_table_names()):
        return
    if "host_key" in {c["name"] for c in inspector.get_columns("servers")}:
        return
    with op.batch_alter_table("servers") as batch:
        batch.add_column(sa.Column("host_key", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("servers") as batch:
        batch.drop_column("host_key")
