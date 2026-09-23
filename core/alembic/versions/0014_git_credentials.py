"""Токены доступа к git-хостингам для пуша и пулла из Лейлы."""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014_git_credentials"
down_revision = "0013_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "git_credentials" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "git_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("secret_ref", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "host", name="uq_git_credentials_owner_host"),
    )
    op.create_index("ix_git_credentials_owner_id", "git_credentials", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_git_credentials_owner_id", table_name="git_credentials")
    op.drop_table("git_credentials")
