"""Возможности моделей, провайдер чата, корзина, папки чатов, умолчания ролей."""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0012_caps_trash_roles"
down_revision = "0011_design_projects"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "providers" in tables and "model_caps" not in _columns(bind, "providers"):
        op.add_column("providers", sa.Column("model_caps", sa.JSON(), nullable=True))

    if "chats" in tables:
        cols = _columns(bind, "chats")
        with op.batch_alter_table("chats") as batch:
            if "provider_id" not in cols:
                batch.add_column(sa.Column("provider_id", sa.String(36), nullable=True))
                batch.create_foreign_key("fk_chats_provider_id", "providers", ["provider_id"], ["id"],
                                         ondelete="SET NULL")
            if "deleted_at" not in cols:
                batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        if "ix_chats_deleted_at" not in _indexes(bind, "chats"):
            op.create_index("ix_chats_deleted_at", "chats", ["deleted_at"])

    if "projects" in tables:
        cols = _columns(bind, "projects")
        with op.batch_alter_table("projects") as batch:
            if "kind" not in cols:
                batch.add_column(sa.Column("kind", sa.String(20), nullable=False, server_default="project"))
            if "deleted_at" not in cols:
                batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        if "ix_projects_deleted_at" not in _indexes(bind, "projects"):
            op.create_index("ix_projects_deleted_at", "projects", ["deleted_at"])
        # Рабочие папки чатов, созданные раньше, — это не проекты «Кода».
        # Узнаём их по имени, которое давал run_chat, и по тому, что ими
        # пользуются только чаты вне «Кода» и ни один макет.
        if {"chats", "designs"} <= tables:
            bind.execute(sa.text("""
                UPDATE projects SET kind = 'chat_workspace'
                WHERE repo_url IS NULL
                  AND (name LIKE 'DESIGN · %' OR name LIKE 'OSINT · %' OR name LIKE 'PENTEST · %')
                  AND EXISTS (SELECT 1 FROM chats c WHERE c.project_id = projects.id
                              AND CAST(c.domain AS TEXT) <> 'code')
                  AND NOT EXISTS (SELECT 1 FROM chats c WHERE c.project_id = projects.id
                                  AND CAST(c.domain AS TEXT) = 'code')
                  AND NOT EXISTS (SELECT 1 FROM designs d WHERE d.project_id = projects.id)
            """))

    if "personas" in tables:
        cols = _columns(bind, "personas")
        with op.batch_alter_table("personas") as batch:
            if "default_model" not in cols:
                batch.add_column(sa.Column("default_model", sa.String(200), nullable=True))
            if "default_mode" not in cols:
                batch.add_column(sa.Column("default_mode", sa.String(16), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "personas" in tables:
        with op.batch_alter_table("personas") as batch:
            batch.drop_column("default_mode")
            batch.drop_column("default_model")
    if "projects" in tables:
        op.drop_index("ix_projects_deleted_at", table_name="projects")
        with op.batch_alter_table("projects") as batch:
            batch.drop_column("deleted_at")
            batch.drop_column("kind")
    if "chats" in tables:
        op.drop_index("ix_chats_deleted_at", table_name="chats")
        with op.batch_alter_table("chats") as batch:
            batch.drop_constraint("fk_chats_provider_id", type_="foreignkey")
            batch.drop_column("deleted_at")
            batch.drop_column("provider_id")
    if "providers" in tables:
        op.drop_column("providers", "model_caps")
