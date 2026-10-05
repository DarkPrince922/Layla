from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlsplit


class TelegramConfigIn(BaseModel):
    bot_token: str | None = Field(default=None, min_length=1, max_length=512, repr=False)
    default_chat_id: str | None = None
    enabled: bool = True
    control_enabled: bool = True
    notify_done: bool = True
    notify_error: bool = True
    notify_cancelled: bool = True
    notify_approval: bool = True
    public_url: str | None = Field(default=None, max_length=1024)

    @field_validator("public_url")
    @classmethod
    def validate_public_url(cls, value):
        if not value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Укажите HTTPS-адрес Лейлы без логина, параметров и фрагмента")
        return value.rstrip("/")


class TelegramConfigOut(BaseModel):
    configured: bool
    enabled: bool = False
    default_chat_id: str | None = None
    token_masked: str | None = None
    control_enabled: bool = False
    connected: bool = False
    bot_username: str | None = None
    notify_done: bool = True
    notify_error: bool = True
    notify_cancelled: bool = True
    notify_approval: bool = True
    public_url: str | None = None
    last_error: str | None = None


class TelegramTestRequest(BaseModel):
    chat_id: str | None = None
    text: str = "Layla: тестовое сообщение ✅"
