"""Account OAuth flow, owner isolation, refresh and subscription inference."""
import json
import time

import httpx
import pytest
from sqlalchemy import select

from app.models.grok_login import GrokLogin
from app.models.provider import Provider
from app.security import crypto
from app.services import grok_oauth, provider_client, tool_chat
from app.services.provider_errors import ProviderError, OutputLimitError


async def register(client):
    await client.post("/api/auth/register", json={"email": "grok@example.com", "password": "hunter2hunter2"})


async def ready(db_sessionmaker, login_id):
    async with db_sessionmaker() as session:
        login = await session.get(GrokLogin, login_id)
        login.next_poll = 0
        await session.commit()


@pytest.fixture
def oauth(monkeypatch):
    async def device():
        return {"device_code": "private-device", "user_code": "ABCD-EFGH",
                "verification_uri": "https://accounts.x.ai/device", "expires_in": 600, "interval": 5}

    async def exchange(data):
        assert data["device_code"] == "private-device"
        return {"access_token": "private-access", "refresh_token": "private-refresh", "expires_in": 900}

    async def models(provider, key):
        assert provider.base_url == grok_oauth.BASE_URL
        assert grok_oauth.wire_credentials(key)["access_token"] == "private-access"
        return ["grok-4", "grok-4", "grok-4-fast", "grok-imagine-image"]

    monkeypatch.setattr(grok_oauth, "request_device", device)
    monkeypatch.setattr(grok_oauth, "exchange", exchange)
    monkeypatch.setattr(provider_client, "list_models", models)


@pytest.mark.asyncio
async def test_account_connect_and_reconnect(client, oauth, db_sessionmaker):
    await register(client)
    started = await client.post("/api/providers/connect/grok/start")
    assert started.status_code == 200
    login = started.json()
    assert login["user_code"] == "ABCD-EFGH"
    assert "private-device" not in started.text
    assert (await client.get("/api/providers")).json() == []
    # Server enforces the poll interval even if a client polls early.
    early = await client.post(f"/api/providers/connect/grok/{login['login_id']}/poll")
    assert early.json()["status"] == "pending"
    await ready(db_sessionmaker, login["login_id"])
    done = await client.post(f"/api/providers/connect/grok/{login['login_id']}/poll")
    assert done.status_code == 200
    pid = done.json()["provider_id"]
    assert "private-access" not in done.text
    assert {m["name"] for m in (await client.get("/api/models")).json()} == {"grok-4", "grok-4-fast"}
    async with db_sessionmaker() as session:
        saved = await session.get(Provider, pid)
        assert "private-access" not in saved.secret_ref
        assert grok_oauth.read_credentials(saved.secret_ref)["refresh_token"] == "private-refresh"
        assert (await session.get(GrokLogin, login["login_id"])).secret_ref != "private-device"
    await client.put(f"/api/providers/{pid}/models", json={"models": [
        {"name": "grok-4", "enabled": False}, {"name": "grok-4-fast", "enabled": True}]})
    started2 = (await client.post("/api/providers/connect/grok/start")).json()
    await ready(db_sessionmaker, started2["login_id"])
    again = await client.post(f"/api/providers/connect/grok/{started2['login_id']}/poll")
    assert again.json()["provider_id"] == pid
    assert len((await client.get("/api/providers")).json()) == 1
    assert [m["name"] for m in (await client.get("/api/models")).json()] == ["grok-4-fast"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error, expected", [("authorization_pending", "pending"), ("slow_down", "pending"),
                                             ("access_denied", "denied"), ("expired_token", "expired")])
async def test_device_status(client, oauth, monkeypatch, db_sessionmaker, error, expected):
    await register(client)
    login = (await client.post("/api/providers/connect/grok/start")).json()
    await ready(db_sessionmaker, login["login_id"])

    async def exchange(data):
        return {"error": error}

    monkeypatch.setattr(grok_oauth, "exchange", exchange)
    result = (await client.post(f"/api/providers/connect/grok/{login['login_id']}/poll")).json()
    assert result["status"] == expected
    if error == "slow_down":
        assert result["interval"] == 10
    assert (await client.get("/api/providers")).json() == []


@pytest.mark.asyncio
async def test_expired_and_owner_isolation(client, oauth, db_sessionmaker):
    await register(client)
    login = (await client.post("/api/providers/connect/grok/start")).json()
    async with db_sessionmaker() as session:
        pending = await session.get(GrokLogin, login["login_id"])
        pending.expires_at = 0
        await session.commit()
    result = await client.post(f"/api/providers/connect/grok/{login['login_id']}/poll")
    assert result.json()["status"] == "expired"
    login = (await client.post("/api/providers/connect/grok/start")).json()
    client.cookies.clear()
    await client.post("/api/auth/register", json={"email": "other@example.com", "password": "hunter2hunter2"})
    assert (await client.post(f"/api/providers/connect/grok/{login['login_id']}/poll")).status_code == 404
    await client.delete(f"/api/providers/connect/grok/{login['login_id']}")
    async with db_sessionmaker() as session:
        assert await session.get(GrokLogin, login["login_id"]) is not None


@pytest.mark.asyncio
async def test_cancel_and_requires_login(client, oauth):
    assert (await client.post("/api/providers/connect/grok/start")).status_code == 401
    await register(client)
    login = (await client.post("/api/providers/connect/grok/start")).json()
    assert (await client.delete(f"/api/providers/connect/grok/{login['login_id']}")).status_code == 204
    assert (await client.post(f"/api/providers/connect/grok/{login['login_id']}/poll")).status_code == 404


@pytest.mark.asyncio
async def test_model_discovery_can_be_retried(client, oauth, monkeypatch, db_sessionmaker):
    await register(client)
    login = (await client.post("/api/providers/connect/grok/start")).json()
    await ready(db_sessionmaker, login["login_id"])

    async def broken(provider, key):
        raise httpx.ConnectError("upstream-private-detail")

    monkeypatch.setattr(provider_client, "list_models", broken)
    response = await client.post(f"/api/providers/connect/grok/{login['login_id']}/poll")
    assert response.json()["status"] == "connected"
    assert response.json()["warning"]
    pid = response.json()["provider_id"]
    assert (await client.get("/api/models")).json() == []
    failed = await client.post(f"/api/providers/{pid}/fetch-models")
    assert failed.status_code == 502
    assert "upstream-private-detail" not in failed.text

    async def models(provider, key):
        return ["grok-4", "grok-imagine-image"]

    monkeypatch.setattr(provider_client, "list_models", models)
    assert (await client.post(f"/api/providers/{pid}/fetch-models")).status_code == 200
    assert [m["name"] for m in (await client.get("/api/models")).json()] == ["grok-4"]


@pytest.mark.asyncio
async def test_refresh_is_persisted(client, oauth, monkeypatch, db_sessionmaker):
    await register(client)
    login = (await client.post("/api/providers/connect/grok/start")).json()
    await ready(db_sessionmaker, login["login_id"])
    pid = (await client.post(f"/api/providers/connect/grok/{login['login_id']}/poll")).json()["provider_id"]
    async def refresh(data):
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "private-refresh"
        return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 900}
    monkeypatch.setattr(grok_oauth, "exchange", refresh)
    async with db_sessionmaker() as session:
        provider = await session.get(Provider, pid)
        data = grok_oauth.read_credentials(provider.secret_ref)
        data["expires_at"] = 0
        provider.secret_ref = crypto.encrypt(json.dumps(data))
        await session.commit()
        assert grok_oauth.wire_credentials(await provider_client.pick_key(session, provider))["access_token"] == "new-access"
    async with db_sessionmaker() as session:
        saved = await session.get(Provider, pid)
        assert grok_oauth.read_credentials(saved.secret_ref)["refresh_token"] == "new-refresh"


@pytest.mark.asyncio
async def test_device_protocol_and_safe_url(monkeypatch):
    real_client = httpx.AsyncClient
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"device_code": "secret", "user_code": "CODE", "expires_in": 600,
            "verification_uri": "https://accounts.x.ai/device" if len(seen) == 1 else "https://evil.example/login"})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(grok_oauth.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    await grok_oauth.request_device()
    assert str(seen[0].url) == "https://auth.x.ai/oauth2/device/code"
    assert b"offline_access" in seen[0].content
    with pytest.raises(ValueError):
        await grok_oauth.request_device()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["response.completed", "response.incomplete", "disconnect"])
async def test_responses_tools_are_atomic(monkeypatch, finish):
    real_client = httpx.AsyncClient
    events = [
        {"type": "response.output_text.delta", "delta": "Проверю"},
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"q":"test"}'},
    ]
    if finish != "disconnect":
        events.append({"type": finish, "response": {"status": "completed"}})
    def handle(request):
        assert str(request.url) == "https://cli-chat-proxy.grok.com/v1/responses"
        assert request.headers["authorization"] == "Bearer access"
        assert request.headers["x-xai-token-auth"] == "xai-grok-cli"
        body = json.loads(request.content)
        assert body["tools"][0]["name"] == "lookup"
        assert body["input"][1]["type"] == "function_call_output"
        return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    provider = Provider(name="Grok", base_url=grok_oauth.BASE_URL)
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
    messages = [{"role": "user", "content": "test"}, {"role": "tool", "tool_call_id": "previous", "content": "ok"}]
    output = []
    async def collect():
        async for item in tool_chat.stream_turn(provider, "grok-oauth:access", "grok-4", messages, tools):
            output.append(item)
    if finish == "response.completed":
        await collect()
        assert output[-1] == ("tool_calls", [{"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"q":"test"}'}}])
    else:
        with pytest.raises(OutputLimitError if finish == "response.incomplete" else ProviderError):
            await collect()
        assert all(kind != "tool_calls" for kind, _ in output)
