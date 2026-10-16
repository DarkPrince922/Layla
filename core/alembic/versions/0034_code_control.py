"""Code plan approval, recovery, memory digests and usage budgets."""
from alembic import op
from app.models.code_control import (
    CodeContextView,
    CodeMemoryDigest,
    CodePlan,
    CodeRecovery,
    CodeUsageCall,
    CodeUsagePolicy,
)

revision = "0034_code_control"
down_revision = "0033_code_mcp_oauth"
branch_labels = None
depends_on = None
TABLES = (CodePlan.__table__, CodeRecovery.__table__, CodeMemoryDigest.__table__,
          CodeUsagePolicy.__table__, CodeUsageCall.__table__, CodeContextView.__table__)


def upgrade():
    for table in TABLES:
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    for table in reversed(TABLES):
        table.drop(op.get_bind(), checkfirst=True)
