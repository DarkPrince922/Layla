"""Durable Code schedules."""
from alembic import op
from app.models.code_schedule import CodeSchedule

revision = "0032_code_schedules"
down_revision = "0031_code_session_archive"
branch_labels = None
depends_on = None


def upgrade():
    CodeSchedule.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CodeSchedule.__table__.drop(op.get_bind(), checkfirst=True)
