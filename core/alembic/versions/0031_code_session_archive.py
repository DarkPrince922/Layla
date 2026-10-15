"""Retain imported source journals outside the visible chat payload."""
from alembic import op
from app.models.code_session import CodeSessionArchive

revision = "0031_code_session_archive"
down_revision = "0030_code_team_requests"
branch_labels = None
depends_on = None


def upgrade():
    CodeSessionArchive.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CodeSessionArchive.__table__.drop(op.get_bind(), checkfirst=True)
