"""Bind jobs to chats and serialize turns while allowing parallel conversations."""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_chat_jobs"
down_revision = "0009_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("jobs")}
    with op.batch_alter_table("jobs") as batch:
        if "chat_id" not in columns:
            batch.add_column(sa.Column("chat_id", sa.String(36), nullable=True))
            batch.create_foreign_key("fk_jobs_chat_id", "chats", ["chat_id"], ["id"], ondelete="SET NULL")
        if "request_id" not in columns:
            batch.add_column(sa.Column("request_id", sa.String(64), nullable=True))
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("jobs")}
    if "ix_jobs_chat_id" not in indexes:
        op.create_index("ix_jobs_chat_id", "jobs", ["chat_id"])
    if "uq_jobs_active_chat" not in indexes:
        condition = sa.text("chat_id IS NOT NULL AND status IN ('queued', 'running')")
        op.create_index("uq_jobs_active_chat", "jobs", ["chat_id"], unique=True,
                        postgresql_where=condition, sqlite_where=condition)
    if "uq_jobs_chat_request" not in indexes:
        op.create_index("uq_jobs_chat_request", "jobs", ["chat_id", "request_id"], unique=True)
    # Only these built-ins gain scoped document output. Read-only/custom personas stay unchanged.
    personas = sa.table("personas", sa.column("id"), sa.column("is_builtin", sa.Boolean),
                        sa.column("kind"), sa.column("allowed_tools", sa.JSON))
    rows = bind.execute(sa.select(personas).where(personas.c.is_builtin.is_(True),
                        personas.c.kind.in_(["pentest", "osint"]))).mappings()
    for row in rows:
        permissions = list(dict.fromkeys([*(row["allowed_tools"] or []), "files.read", "files.write"]))
        bind.execute(personas.update().where(personas.c.id == row["id"]).values(allowed_tools=permissions))


def downgrade() -> None:
    for name in ("uq_jobs_chat_request", "uq_jobs_active_chat", "ix_jobs_chat_id"):
        op.drop_index(name, table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("request_id")
        batch.drop_column("chat_id")
