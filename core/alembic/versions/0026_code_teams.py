"""Code teams, mailboxes and recoverable worker transcripts."""
import sqlalchemy as sa

from alembic import op
from app.models.code_team import CodeTeam, CodeTeamMessage, CodeTranscript

revision = "0026_code_teams"
down_revision = "0025_shell_checkpoints"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    CodeTeam.__table__.create(bind, checkfirst=True)
    columns = {column["name"] for column in sa.inspect(bind).get_columns("code_workers")}
    if "team_id" not in columns:
        with op.batch_alter_table("code_workers", reflect_kwargs={"resolve_fks": False}) as batch:
            batch.add_column(sa.Column("team_id", sa.String(36), nullable=True))
            batch.add_column(sa.Column("member_name", sa.String(80), nullable=True))
            batch.create_foreign_key("fk_code_worker_team", "code_teams", ["team_id"], ["id"], ondelete="SET NULL")
            batch.create_index("ix_code_workers_team_id", ["team_id"])
            batch.create_unique_constraint("uq_code_team_member", ["team_id", "member_name"])
    CodeTeamMessage.__table__.create(bind, checkfirst=True)
    CodeTranscript.__table__.create(bind, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    CodeTranscript.__table__.drop(bind, checkfirst=True)
    CodeTeamMessage.__table__.drop(bind, checkfirst=True)
    with op.batch_alter_table("code_workers", reflect_kwargs={"resolve_fks": False}) as batch:
        batch.drop_constraint("uq_code_team_member", type_="unique")
        batch.drop_index("ix_code_workers_team_id")
        batch.drop_constraint("fk_code_worker_team", type_="foreignkey")
        batch.drop_column("member_name")
        batch.drop_column("team_id")
    CodeTeam.__table__.drop(bind, checkfirst=True)
