"""Отправка сообщений через Telegram Bot API (спец. §5.8)."""
from __future__ import annotations

import httpx


async def send_message(token: str, chat_id: str, text: str, *, timeout: float = 15.0) -> None:
    """Отправить сообщение. Бросает RuntimeError при ошибке API/сети."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": text})
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Сеть недоступна: {exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"Telegram API вернул {resp.status_code}: {resp.text[:200]}")
