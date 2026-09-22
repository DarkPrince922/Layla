"""Admin role, forced-password flag, and first-run admin bootstrap table."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_admin_users"
down_revision = "0007_code_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "users" in inspector.get_table_names():
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        with op.batch_alter_table("users") as batch:
            if "is_admin" not in user_cols:
                batch.add_column(
                    sa.Column(
                        "is_admin", sa.Boolean(), nullable=False, server_default=sa.false()
                    )
                )
            if "must_change_password" not in user_cols:
                batch.add_column(
                    sa.Column(
                        "must_change_password",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )

    if "admin_bootstrap" not in inspector.get_table_names():
        op.create_table(
            "admin_bootstrap",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("password", sa.String(length=255), nullable=False),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "admin_bootstrap" in inspector.get_table_names():
        op.drop_table("admin_bootstrap")
    user_cols = {c["name"] for c in inspector.get_columns("users")}
    with op.batch_alter_table("users") as batch:
        if "must_change_password" in user_cols:
            batch.drop_column("must_change_password")
        if "is_admin" in user_cols:
            batch.drop_column("is_admin")
