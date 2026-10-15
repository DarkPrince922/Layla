"""Bind terminal history to an explicit Code working copy."""
from alembic import op
import sqlalchemy as sa

revision = "0029_code_shell_workspace"
down_revision = "0028_code_workspaces"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # Partial historical fixtures can predate the shell table; the normal
    # installation already has it at revision 0025.
    if "code_shell_runs" not in sa.inspect(bind).get_table_names():
        return
    if "workspace_id" not in {col["name"] for col in sa.inspect(bind).get_columns("code_shell_runs")}:
        op.add_column("code_shell_runs", sa.Column("workspace_id", sa.String(36), nullable=True))
    if "ix_code_shell_runs_workspace_id" not in {idx["name"] for idx in sa.inspect(bind).get_indexes("code_shell_runs")}:
        op.create_index("ix_code_shell_runs_workspace_id", "code_shell_runs", ["workspace_id"])


def downgrade():
    if "code_shell_runs" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("ix_code_shell_runs_workspace_id", table_name="code_shell_runs")
    op.drop_column("code_shell_runs", "workspace_id")
