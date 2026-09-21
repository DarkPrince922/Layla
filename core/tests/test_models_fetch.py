"""Тесты пикера моделей: fallback и fetch у провайдера (спец. §5.2)."""
from __future__ import annotations

import pytest

import app.services.provider_client as pc


async def _setup(client):
    await client.post(
        "/api/auth/register",
        json={"email": "mdl@example.com", "password": "hunter2hunter2"},
    )
    await client.post(
        "/api/providers",
        json={
            "name": "abyss",
            "kind": "openai_compatible",
            "base_url": "http://prov.local/v1",
            "default_model": "abyss/kimi-k3",
            "active": True,
        },
    )


@pytest.mark.asyncio
async def test_models_fallback_to_default(client):
    await _setup(client)
    models = (await client.get("/api/models")).json()
    assert any(m["name"] == "abyss/kimi-k3" for m in models)


@pytest.mark.asyncio
async def test_models_refresh_fetches_provider_list(client, monkeypatch):
    await _setup(client)

    async def fake_list_models(provider, key):
        return ["abyss/kimi-k3", "abyss/gpt-5", "abyss/claude-5"]

    monkeypatch.setattr(pc, "list_models", fake_list_models)

    models = (await client.get("/api/models?refresh=true")).json()
    names = {m["name"] for m in models}
    assert {"abyss/kimi-k3", "abyss/gpt-5", "abyss/claude-5"} <= names
