"""Allow Tavily in the existing integration key store.

Revision ID: 0035_code_tavily
Revises: 0034_code_control
"""
from alembic import op

revision = "0035_code_tavily"
down_revision = "0034_code_control"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE intelprovider ADD VALUE IF NOT EXISTS 'tavily'")


def downgrade():
    # PostgreSQL enum labels cannot be removed safely while old keys may exist.
    # Retain the additive label and encrypted credentials on downgrade.
    pass
