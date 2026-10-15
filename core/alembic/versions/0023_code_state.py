"""Persist Code task graphs and conversation memory."""
from alembic import op
from app.models.code_state import CodeState

revision = "0023_code_state"
down_revision = "0022_telegram_bot"
branch_labels = None
depends_on = None


def upgrade():
    CodeState.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CodeState.__table__.drop(op.get_bind(), checkfirst=True)
