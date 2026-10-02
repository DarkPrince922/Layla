"""Контрольные точки файлов для отката изменений агента."""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_checkpoints"
down_revision = "0012_caps_trash_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "checkpoints" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("chat_id", sa.String(36), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(4096), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_checkpoints_chat_id", "checkpoints", ["chat_id"])
    op.create_index("ix_checkpoints_message_id", "checkpoints", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_checkpoints_message_id", table_name="checkpoints")
    op.drop_index("ix_checkpoints_chat_id", table_name="checkpoints")
    op.drop_table("checkpoints")
