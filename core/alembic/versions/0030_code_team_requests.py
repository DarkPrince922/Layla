"""Durable Code team plan and graceful shutdown decisions."""
from alembic import op
from app.models.code_team import CodeTeamRequest

revision = "0030_code_team_requests"
down_revision = "0029_code_shell_workspace"
branch_labels = None
depends_on = None


def upgrade():
    CodeTeamRequest.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CodeTeamRequest.__table__.drop(op.get_bind(), checkfirst=True)
