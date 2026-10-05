"""Telegram interactive control state and durable notification outbox."""
from alembic import op
from app.models.telegram_bot import TelegramBotState, TelegramNotification

revision = "0022_telegram_bot"
down_revision = "0021_grok_login"
branch_labels = None
depends_on = None


def upgrade():
    TelegramBotState.__table__.create(op.get_bind(), checkfirst=True)
    TelegramNotification.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    TelegramNotification.__table__.drop(op.get_bind(), checkfirst=True)
    TelegramBotState.__table__.drop(op.get_bind(), checkfirst=True)
