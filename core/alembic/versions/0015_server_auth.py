"""Способ входа на attack box: ключ или пароль (столбец servers.auth)."""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015_server_auth"
down_revision = "0014_git_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "servers" not in set(inspector.get_table_names()):
        return  # старые БД без этой таблицы получат её из более поздней схемы
    if "auth" in {c["name"] for c in inspector.get_columns("servers")}:
        return
    # Прежние серверы были только по ключу — значение по умолчанию сохраняет их поведение.
    with op.batch_alter_table("servers") as batch:
        batch.add_column(sa.Column("auth", sa.String(16), nullable=False, server_default="key"))


def downgrade() -> None:
    with op.batch_alter_table("servers") as batch:
        batch.drop_column("auth")
