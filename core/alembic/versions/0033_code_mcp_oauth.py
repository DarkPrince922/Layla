"""Code MCP OAuth and binary results."""
from alembic import op
from app.models.code_mcp import CodeMcpArtifact, CodeMcpAuth

revision = "0033_code_mcp_oauth"
down_revision = "0032_code_schedules"
branch_labels = None
depends_on = None


def upgrade():
    for table in (CodeMcpAuth.__table__, CodeMcpArtifact.__table__):
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    for table in (CodeMcpArtifact.__table__, CodeMcpAuth.__table__):
        table.drop(op.get_bind(), checkfirst=True)
