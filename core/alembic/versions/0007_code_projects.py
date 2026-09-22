"""Bind code chats to projects; compatible with the live-metadata baseline."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_code_projects"
down_revision = "0006_provmodels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("chats")}
    if "project_id" not in columns:
        with op.batch_alter_table("chats") as batch:
            batch.add_column(sa.Column("project_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                "fk_chats_project_id", "projects", ["project_id"], ["id"], ondelete="SET NULL"
            )
            batch.create_index("ix_chats_project_id", ["project_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "project_id" in {c["name"] for c in inspector.get_columns("chats")}:
        with op.batch_alter_table(
            "chats", naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s"}
        ) as batch:
            batch.drop_index("ix_chats_project_id")
            fk = next(
                f
                for f in inspector.get_foreign_keys("chats")
                if f["constrained_columns"] == ["project_id"]
            )
            batch.drop_constraint(fk["name"] or "fk_chats_project_id", type_="foreignkey")
            batch.drop_column("project_id")
