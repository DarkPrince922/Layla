from __future__ import annotations

from pydantic import BaseModel, Field


class TelegramConfigIn(BaseModel):
    bot_token: str = Field(min_length=1)
    default_chat_id: str | None = None
    enabled: bool = True


class TelegramConfigOut(BaseModel):
    configured: bool
    enabled: bool = False
    default_chat_id: str | None = None
    token_masked: str | None = None


class TelegramTestRequest(BaseModel):
    chat_id: str | None = None
    text: str = "Layla: тестовое сообщение ✅"
