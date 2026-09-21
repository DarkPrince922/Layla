"""Тесты чата со стримингом (спец. §5.2), с моком LiteLLM (без сети)."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

import app.services.provider_client as pc


async def _register(client):
    await client.post(
        "/api/auth/register",
        json={"email": "chat@example.com", "password": "hunter2hunter2"},
    )


async def _add_active_provider(client, model="gpt-4o"):
    await client.post(
        "/api/providers",
        json={
            "name": "M",
            "kind": "openai_compatible",
            "base_url": "http://prov.local/v1",
            "default_model": model,
            "active": True,
        },
    )


@pytest.mark.asyncio
async def test_chat_stream_and_persist(client, monkeypatch):
    await _register(client)
    await _add_active_provider(client)

    # Мокаем сетевой стрим провайдера детерминированными дельтами.
    async def fake_stream(provider, key, model, messages, **kw) -> AsyncIterator[str]:
        assert model == "gpt-4o"
        # Системный промпт персоны должен попасть в payload.
        assert messages[0]["role"] == "system"
        for piece in ["При", "вет", "!"]:
            yield piece

    monkeypatch.setattr(pc, "stream_chat", fake_stream)

    # Найдём id встроенной персоны.
    personas = (await client.get("/api/personas")).json()
    persona_id = personas[0]["id"]

    chat = (
        await client.post(
            "/api/chats",
            json={"domain": "code", "model": "gpt-4o", "persona_id": persona_id},
        )
    ).json()

    # Отправка сообщения → SSE-поток.
    r = await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "Скажи привет"})
    assert r.status_code == 200
    body = r.text
    assert "При" in body and "вет" in body
    assert "done" in body

    # История сохранена: user + assistant.
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "Привет!"


@pytest.mark.asyncio
async def test_send_without_model_rejected(client):
    await _register(client)
    chat = (await client.post("/api/chats", json={"domain": "code"})).json()
    r = await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "hi"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_chat_isolation_between_users(client):
    await _register(client)
    chat = (await client.post("/api/chats", json={"domain": "code", "model": "m"})).json()
    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/register",
        json={"email": "other@example.com", "password": "hunter2hunter2"},
    )
    r = await client.get(f"/api/chats/{chat['id']}")
    assert r.status_code == 404
