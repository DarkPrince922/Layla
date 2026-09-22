"""Background jobs table (cross-domain progress / reasoning / steps)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_jobs"
down_revision = "0008_admin_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "jobs" in inspector.get_table_names():
        return
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(length=32), nullable=False, server_default="code"),
        sa.Column("kind", sa.String(length=64), nullable=False, server_default="task"),
        sa.Column("title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reasoning", sa.Text(), nullable=False, server_default=""),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_jobs_owner_id", "jobs", ["owner_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "jobs" in inspector.get_table_names():
        op.drop_index("ix_jobs_status", table_name="jobs")
        op.drop_index("ix_jobs_owner_id", table_name="jobs")
        op.drop_table("jobs")
