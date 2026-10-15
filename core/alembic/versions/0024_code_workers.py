"""Isolated Code workers and persistent background output."""
import sqlalchemy as sa

from alembic import op

revision = "0024_code_workers"
down_revision = "0023_code_state"
branch_labels = None
depends_on = None


def upgrade():
    # Freeze this revision: later worker foreign keys must not be installed
    # before their referenced tables exist on an incremental upgrade.
    metadata = sa.MetaData()
    for name in ("users", "chats", "projects", "jobs"):
        sa.Table(name, metadata, sa.Column("id", sa.String(36), primary_key=True))
    table = sa.Table("code_workers", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        *(sa.Column(name + "_id", sa.String(36), sa.ForeignKey(target + ".id", ondelete=action), index=index, nullable=name == "job")
          for name, target, action, index in (("owner", "users", "CASCADE", True),
              ("chat", "chats", "CASCADE", True), ("project", "projects", "CASCADE", True),
              ("job", "jobs", "SET NULL", False))),
        sa.Column("slot", sa.Integer()), sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("role", sa.String(16), nullable=False), sa.Column("task", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        *(sa.Column(name, sa.Text(), nullable=name == "error") for name in ("output", "report", "error")),
        *(sa.Column(name, sa.JSON(), nullable=False) for name in ("events", "payload", "changes", "applied", "summary")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("chat_id", "slot", name="uq_code_worker_slot"))
    table.create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("code_workers")
