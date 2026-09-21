"""M3: OSINT observations/timeline and encrypted MCP environments.

0001 uses live metadata, so inspect before adding objects for fresh installs.
Existing M2 databases receive these changes through ordinary DDL.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op
from app.security import crypto

revision = "0003_m3"
down_revision = "0002_m2"
branch_labels = None
depends_on = None


def _identity():
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "case_id",
            sa.String(36),
            sa.ForeignKey("osint_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if "osint_lookups" not in tables:
        op.create_table(
            "osint_lookups",
            *_identity(),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("target", sa.String(253), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("error_code", sa.String(40)),
            sa.Column("error", sa.Text()),
            sa.Column("artifact_count", sa.Integer(), nullable=False),
            sa.Column("duplicate_count", sa.Integer(), nullable=False),
        )
        op.create_index("ix_osint_lookups_case_id", "osint_lookups", ["case_id"])
    if "osint_artifacts" not in tables:
        op.create_table(
            "osint_artifacts",
            *_identity(),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("target", sa.String(500), nullable=False),
            sa.Column("kind", sa.String(40), nullable=False),
            sa.Column("title", sa.String(300), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("source_url", sa.String(2048), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.UniqueConstraint("case_id", "fingerprint", name="uq_osint_artifact"),
        )
        op.create_index("ix_osint_artifacts_case_id", "osint_artifacts", ["case_id"])
    columns = {c["name"] for c in sa.inspect(bind).get_columns("mcp_servers")}
    if "env_secret_ref" not in columns:
        op.add_column("mcp_servers", sa.Column("env_secret_ref", sa.Text(), nullable=True))
    table = sa.table(
        "mcp_servers",
        sa.column("id", sa.String),
        sa.column("env", sa.JSON),
        sa.column("env_secret_ref", sa.Text),
    )
    for row in bind.execute(sa.select(table)).mappings():
        if row["env"]:
            encrypted = row["env_secret_ref"] or crypto.encrypt(json.dumps(row["env"]))
            bind.execute(
                table.update()
                .where(table.c.id == row["id"])
                .values(
                    env={},
                    env_secret_ref=encrypted,
                )
            )


def downgrade() -> None:
    # Keep encrypted environments on downgrade; never restore plaintext secrets.
    # Older versions see an empty environment and require reconfiguration.
    op.drop_table("osint_artifacts")
    op.drop_table("osint_lookups")
