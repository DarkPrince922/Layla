"""Isolated Code workers and persistent background output."""
from alembic import op
from app.models.code_worker import CodeWorker

revision = "0024_code_workers"
down_revision = "0023_code_state"
branch_labels = None
depends_on = None


def upgrade():
    CodeWorker.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CodeWorker.__table__.drop(op.get_bind(), checkfirst=True)
