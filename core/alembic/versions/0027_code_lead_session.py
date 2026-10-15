"""Recover the lead's Code protocol across turns and restarts."""
from alembic import op
from app.models.code_session import CodeLeadEntry, CodeLeadTurn

revision = "0027_code_lead_session"
down_revision = "0026_code_teams"
branch_labels = None
depends_on = None


def upgrade():
    CodeLeadTurn.__table__.create(op.get_bind(), checkfirst=True)
    CodeLeadEntry.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CodeLeadEntry.__table__.drop(op.get_bind(), checkfirst=True)
    CodeLeadTurn.__table__.drop(op.get_bind(), checkfirst=True)
