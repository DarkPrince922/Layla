"""Связь макета из домена Design с проектом домена Код."""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_design_projects"
down_revision = "0010_chat_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # Часть старых баз доходит сюда без таблицы макетов — она создаётся метаданными.
    if "designs" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("designs")}
    if "project_id" not in columns:
        with op.batch_alter_table("designs") as batch:
            batch.add_column(sa.Column("project_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                "fk_designs_project_id", "projects", ["project_id"], ["id"], ondelete="SET NULL"
            )
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("designs")}
    if "ix_designs_project_id" not in indexes:
        op.create_index("ix_designs_project_id", "designs", ["project_id"])


def downgrade() -> None:
    if "designs" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("ix_designs_project_id", table_name="designs")
    with op.batch_alter_table("designs") as batch:
        batch.drop_constraint("fk_designs_project_id", type_="foreignkey")
        batch.drop_column("project_id")
