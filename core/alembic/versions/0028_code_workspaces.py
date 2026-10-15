"""Lead workspace transitions and correctly scoped file rollback."""
from alembic import op
from app.models.code_workspace import CodeWorkspace, CodeWorkspaceCheckpoint

revision = "0028_code_workspaces"
down_revision = "0027_code_lead_session"
branch_labels = None
depends_on = None


def upgrade():
    CodeWorkspace.__table__.create(op.get_bind(), checkfirst=True)
    CodeWorkspaceCheckpoint.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CodeWorkspaceCheckpoint.__table__.drop(op.get_bind(), checkfirst=True)
    CodeWorkspace.__table__.drop(op.get_bind(), checkfirst=True)
