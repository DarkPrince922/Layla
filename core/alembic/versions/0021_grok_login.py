"""Persist encrypted Grok device authorizations across workers and restarts."""
from alembic import op
from app.models.grok_login import GrokLogin

revision = "0021_grok_login"
down_revision = "0020_pentest_workbench"
branch_labels = None
depends_on = None


def upgrade():
    GrokLogin.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    GrokLogin.__table__.drop(op.get_bind(), checkfirst=True)
