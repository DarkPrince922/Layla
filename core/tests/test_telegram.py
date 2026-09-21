"""Тесты интеграции Telegram (спец. §5.8) — токен не утекает, тест-отправка."""
from __future__ import annotations

import pytest

import app.services.telegram as tg


async def _register(client):
    await client.post(
        "/api/auth/register",
        json={"email": "tg@example.com", "password": "hunter2hunter2"},
    )


@pytest.mark.asyncio
async def test_configure_and_status_masks_token(client):
    await _register(client)
    secret = "123456:AA-SECRET-BOT-TOKEN"
    r = await client.put(
        "/api/integrations/telegram",
        json={"bot_token": secret, "default_chat_id": "42", "enabled": True},
    )
    assert r.status_code == 200
    assert secret not in r.text
    body = r.json()
    assert body["configured"] is True
    assert body["default_chat_id"] == "42"
    assert body["token_masked"] and secret not in body["token_masked"]

    status = (await client.get("/api/integrations/telegram")).json()
    assert status["configured"] is True
    assert secret not in str(status)


@pytest.mark.asyncio
async def test_test_send_uses_service(client, monkeypatch):
    await _register(client)
    await client.put(
        "/api/integrations/telegram",
        json={"bot_token": "t", "default_chat_id": "99"},
    )

    sent = {}

    async def fake_send(token, chat_id, text, **kw):
        sent["chat_id"] = chat_id
        sent["text"] = text

    monkeypatch.setattr(tg, "send_message", fake_send)
    r = await client.post("/api/integrations/telegram/test", json={"text": "привет"})
    assert r.status_code == 200
    assert sent["chat_id"] == "99"
    assert sent["text"] == "привет"


@pytest.mark.asyncio
async def test_test_send_without_config_rejected(client):
    await _register(client)
    r = await client.post("/api/integrations/telegram/test", json={})
    assert r.status_code == 400
