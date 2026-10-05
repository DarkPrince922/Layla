"""Grok connection validates credentials before publishing chat models."""
import httpx
import pytest
from sqlalchemy import select

from app.models.provider import Provider
from app.security import crypto
from app.services import provider_client


async def register(client):
    response = await client.post("/api/auth/register", json={
        "email": "grok@example.com", "password": "hunter2hunter2",
    })
    assert response.status_code < 300


@pytest.mark.asyncio
async def test_connect_and_reconnect(client, monkeypatch, db_sessionmaker):
    await register(client)
    keys = []

    async def models(provider, key):
        assert provider.base_url == "https://api.x.ai/v1"
        assert provider.kind.value == "openai_compatible"
        keys.append(key)
        return ["grok-4", "grok-4", "grok-4-fast", "grok-imagine-image", "other"]

    monkeypatch.setattr(provider_client, "list_models", models)
    response = await client.post("/api/providers/connect/grok", json={"api_key": " xai-test "})
    assert response.status_code == 200
    provider = response.json()
    assert "xai-test" not in response.text
    pid = provider["id"]
    assert {m["name"] for m in (await client.get("/api/models")).json()} == {"grok-4", "grok-4-fast"}
    await client.put(f"/api/providers/{pid}/models", json={"models": [
        {"name": "grok-4", "enabled": False}, {"name": "grok-4-fast", "enabled": True},
    ]})
    again = await client.post("/api/providers/connect/grok", json={"api_key": "xai-new"})
    assert again.status_code == 200
    assert again.json()["id"] == pid
    assert len((await client.get("/api/providers")).json()) == 1
    assert [m["name"] for m in (await client.get("/api/models")).json()] == ["grok-4-fast"]
    assert keys == ["xai-test", "xai-new"]
    async with db_sessionmaker() as session:
        saved = await session.scalar(select(Provider).where(Provider.id == pid))
        assert saved.secret_ref != "xai-new"
        assert crypto.decrypt(saved.secret_ref) == "xai-new"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_upstream_errors_do_not_save_key(client, monkeypatch, status):
    await register(client)

    async def models(provider, key):
        request = httpx.Request("GET", "https://api.x.ai/v1/models")
        response = httpx.Response(status, request=request, text="sensitive-upstream-detail")
        response.raise_for_status()

    monkeypatch.setattr(provider_client, "list_models", models)
    response = await client.post("/api/providers/connect/grok", json={"api_key": "xai-secret"})
    assert response.status_code == (422 if status in (401, 403) else 429 if status == 429 else 502)
    assert "xai-secret" not in response.text
    assert "sensitive-upstream-detail" not in response.text
    assert (await client.get("/api/providers")).json() == []


@pytest.mark.asyncio
async def test_no_chat_models_does_not_create_provider(client, monkeypatch):
    await register(client)

    async def models(provider, key):
        return ["grok-imagine-image", "grok-imagine-video"]

    monkeypatch.setattr(provider_client, "list_models", models)
    response = await client.post("/api/providers/connect/grok", json={"api_key": "xai-test"})
    assert response.status_code == 422
    assert (await client.get("/api/providers")).json() == []


@pytest.mark.asyncio
async def test_connect_requires_login(client):
    response = await client.post("/api/providers/connect/grok", json={"api_key": "xai-test"})
    assert response.status_code == 401
