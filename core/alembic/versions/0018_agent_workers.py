"""Durable independent pentest workers and step attribution."""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.models.agent import AgentWorker

revision = "0018_agent_workers"
down_revision = "0017_offensive_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "agent_runs" not in set(inspector.get_table_names()):
        return
    AgentWorker.__table__.create(bind, checkfirst=True)
    if "worker_id" not in {c["name"] for c in inspector.get_columns("agent_steps")}:
        with op.batch_alter_table("agent_steps") as batch:
            batch.add_column(sa.Column("worker_id", sa.String(36), nullable=True))
            batch.create_foreign_key("fk_agent_steps_worker", "agent_workers", ["worker_id"], ["id"], ondelete="SET NULL")
            batch.create_index("ix_agent_steps_worker_id", ["worker_id"])


def downgrade() -> None:
    with op.batch_alter_table("agent_steps") as batch:
        batch.drop_index("ix_agent_steps_worker_id")
        batch.drop_constraint("fk_agent_steps_worker", type_="foreignkey")
        batch.drop_column("worker_id")
    AgentWorker.__table__.drop(op.get_bind(), checkfirst=True)
