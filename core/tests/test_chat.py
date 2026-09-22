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

    # Мокаем сетевой стрим провайдера: сначала reasoning, затем контент.
    async def fake_stream(provider, key, model, messages, **kw) -> AsyncIterator[tuple[str, str]]:
        assert model == "gpt-4o"
        # Системный промпт персоны должен попасть в payload.
        assert messages[0]["role"] == "system"
        yield ("reasoning", "думаю…")
        for piece in ["При", "вет", "!"]:
            yield ("content", piece)

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


@pytest.mark.asyncio
async def test_delete_chat_in_every_domain(client):
    """Удаление чата доступно во всех доменах и убирает его из истории."""
    await _register(client)
    for domain in ("code", "design", "osint", "pentest"):
        chat = (await client.post("/api/chats", json={"domain": domain, "model": "m"})).json()
        assert (await client.delete(f"/api/chats/{chat['id']}")).status_code == 204
        assert (await client.get(f"/api/chats/{chat['id']}")).status_code == 404
        listed = (await client.get(f"/api/chats?domain={domain}")).json()
        assert all(c["id"] != chat["id"] for c in listed)


@pytest.mark.asyncio
async def test_cannot_delete_foreign_chat(client):
    """Чужой чат удалить нельзя — изоляция по владельцу."""
    await _register(client)
    chat = (await client.post("/api/chats", json={"domain": "osint", "model": "m"})).json()
    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/register",
        json={"email": "other@example.com", "password": "hunter2hunter2"},
    )
    assert (await client.delete(f"/api/chats/{chat['id']}")).status_code == 404
