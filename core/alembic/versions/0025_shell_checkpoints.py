"""Binary and executable file rollback for Code shell changes."""
import sqlalchemy as sa

from alembic import op
from app.models.code_shell import CodeShellRun

revision = "0025_shell_checkpoints"
down_revision = "0024_code_workers"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("checkpoints")}
    if "encoding" not in columns:
        op.add_column("checkpoints", sa.Column("encoding", sa.String(16), nullable=False, server_default="utf-8"))
    if "mode" not in columns:
        op.add_column("checkpoints", sa.Column("mode", sa.Integer(), nullable=True))
    CodeShellRun.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CodeShellRun.__table__.drop(op.get_bind(), checkfirst=True)
    op.drop_column("checkpoints", "mode")
    op.drop_column("checkpoints", "encoding")
