import asyncio
import base64
import hashlib
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from app.models.code_mcp import CodeMcpAuth
from app.models.mcp import McpServer
from app.services import code_mcp_oauth as oauth
from tests.test_code_runtime import setup_code


@pytest.fixture
def oauth_server(monkeypatch):
    calls = []
    async def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if kwargs.get("probe"):
            return 401, {"www-authenticate": 'Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource", scope="docs:read"'}, {}
        if "oauth-protected-resource" in url:
            return 200, {}, {"resource": "https://mcp.example/mcp", "authorization_servers": ["https://auth.example"]}
        if "oauth-authorization-server" in url:
            return 200, {}, {"issuer": "https://auth.example", "authorization_endpoint": "https://auth.example/authorize",
                "token_endpoint": "https://auth.example/token", "revocation_endpoint": "https://auth.example/revoke",
                "code_challenge_methods_supported": ["S256"], "authorization_response_iss_parameter_supported": True}
        if url.endswith("/token"):
            await asyncio.sleep(0.01)
            refresh = kwargs["data"]["grant_type"] == "refresh_token"
            return 200, {}, {"access_token": "refreshed-secret" if refresh else "access-secret", "token_type": "Bearer",
                             "refresh_token": "rotated-secret" if refresh else "refresh-secret", "expires_in": 3600}
        if url.endswith("/revoke"):
            return 200, {}, {}
        return 404, {}, {}
    monkeypatch.setattr(oauth, "request", request)
    return calls


async def start(client):
    owner, _, _ = await setup_code(client)
    server = (await client.post("/api/mcp/servers", json={"name": "Protected Code", "transport": "http", "url": "https://mcp.example/mcp"})).json()
    result = await client.post(f"/api/code/mcp/{server['id']}/oauth", json={"client_id": "registered-client"})
    assert result.status_code == 200, result.text
    query = parse_qs(urlsplit(result.json()["authorization_url"]).query)
    return owner, server, query


async def finish(client, query):
    response = await client.get("/api/code/mcp/oauth/callback?" + urlencode({"state": query["state"][0], "code": "auth-code", "iss": "https://auth.example"}), follow_redirects=False)
    assert response.status_code == 303, response.text


async def test_pkce_resource_encryption_callback_replay_and_disconnect(client, db_sessionmaker, oauth_server):
    _, server, query = await start(client)
    assert query["code_challenge_method"] == ["S256"] and query["resource"] == ["https://mcp.example/mcp"]
    assert query["scope"] == ["docs:read"]
    async with db_sessionmaker() as session:
        row = await session.get(CodeMcpAuth, server["id"])
        verifier = oauth.decrypt(row.pending_secret)["verifier"]
        assert query["code_challenge"][0] == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        assert verifier not in row.pending_secret and query["state"][0] != row.state_hash
    await finish(client, query)
    output = await client.get("/api/code/mcp/oauth")
    assert output.json()[server["id"]] == "connected" and "secret" not in output.text
    replay = await client.get("/api/code/mcp/oauth/callback?" + urlencode({"state": query["state"][0], "code": "auth-code", "iss": "https://auth.example"}))
    assert replay.status_code == 400
    assert sum(url.endswith("/token") for _, url, _ in oauth_server) == 1
    assert (await client.delete(f"/api/code/mcp/{server['id']}/oauth")).status_code == 200
    async with db_sessionmaker() as session:
        assert await session.get(CodeMcpAuth, server["id"]) is None


async def test_mismatched_issuer_and_other_owner_never_exchange_code(client, db_sessionmaker, oauth_server):
    _, server, query = await start(client)
    bad = await client.get("/api/code/mcp/oauth/callback?" + urlencode({"state": query["state"][0], "code": "auth-code", "iss": "https://evil.example"}))
    assert bad.status_code == 400
    await client.post("/api/auth/register", json={"email": "oauth-other@example.com", "password": "hunter2hunter2"})
    response = await client.get("/api/code/mcp/oauth/callback?" + urlencode({"state": query["state"][0], "code": "auth-code", "iss": "https://auth.example"}))
    assert response.status_code == 400
    assert (await client.delete(f"/api/code/mcp/{server['id']}/oauth")).status_code == 404
    assert not any(url.endswith("/token") for _, url, _ in oauth_server)


async def test_refresh_rotation_is_serialized_and_server_url_change_blocks_tokens(client, db_sessionmaker, oauth_server):
    _, server, query = await start(client)
    await finish(client, query)
    async with db_sessionmaker() as session:
        row = await session.get(CodeMcpAuth, server["id"])
        saved = oauth.decrypt(row.tokens_secret)
        saved["expires_at"] = time.time() - 1
        row.tokens_secret = oauth.encrypt(saved)
        await session.commit()
        model = await session.get(McpServer, server["id"])
    results = await asyncio.gather(*(oauth.access_token(db_sessionmaker, model) for _ in range(3)))
    assert results == ["refreshed-secret"] * 3
    assert sum(url.endswith("/token") and kw["data"]["grant_type"] == "refresh_token" for _, url, kw in oauth_server) == 1
    async with db_sessionmaker() as session:
        row = await session.get(CodeMcpAuth, server["id"])
        assert oauth.decrypt(row.tokens_secret)["refresh_token"] == "rotated-secret"
        assert "rotated-secret" not in row.tokens_secret
        changed = await session.get(McpServer, server["id"])
        changed.url = "https://other.example/mcp"
        await session.commit()
    with pytest.raises(ValueError):
        await oauth.access_token(db_sessionmaker, model)


@pytest.mark.parametrize("url", ["http://public.example", "https://user:pass@example.com", "https://example.com/#fragment"])
def test_invalid_oauth_endpoints(url):
    with pytest.raises(ValueError):
        oauth.validate_url(url)


@pytest.mark.parametrize("registration", ["metadata", "dynamic"])
async def test_automatic_client_registration_methods(client, monkeypatch, oauth_server, registration):
    from app.api import code_mcp as api
    monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(public_url="https://layla.example", env="dev"))
    old_request = oauth.request
    async def request(method, url, **kwargs):
        if url.endswith("/register"):
            body = json.loads(kwargs["data"])
            assert body["token_endpoint_auth_method"] == "none"
            assert body["redirect_uris"] == ["https://layla.example/api/code/mcp/oauth/callback"]
            return 201, {}, {"client_id": "dynamic-client", "token_endpoint_auth_method": "none"}
        code, headers, body = await old_request(method, url, **kwargs)
        if "oauth-authorization-server" in url:
            body["client_id_metadata_document_supported"] = registration == "metadata"
            body["registration_endpoint"] = "https://auth.example/register"
        return code, headers, body
    monkeypatch.setattr(oauth, "request", request)
    await setup_code(client)
    server = (await client.post("/api/mcp/servers", json={"name": "Auto OAuth", "transport": "http", "url": "https://mcp.example/mcp"})).json()
    response = await client.post(f"/api/code/mcp/{server['id']}/oauth", json={})
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    expected = "https://layla.example/api/code/mcp/client-metadata" if registration == "metadata" else "dynamic-client"
    assert query["client_id"] == [expected]
    metadata = (await client.get("/api/code/mcp/client-metadata")).json()
    assert metadata["redirect_uris"] == ["https://layla.example/api/code/mcp/oauth/callback"]


async def test_reauthorization_reuses_registered_client_and_invalidates_old_state(client, oauth_server):
    _, server, old = await start(client)
    response = await client.post(f"/api/code/mcp/{server['id']}/oauth", json={})
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    assert query["client_id"] == ["registered-client"] and query["state"] != old["state"]
    await finish(client, query)


async def test_revoke_during_refresh_cannot_resurrect_tokens(client, db_sessionmaker, monkeypatch, oauth_server):
    _, server, query = await start(client)
    await finish(client, query)
    old_request = oauth.request
    started, release = asyncio.Event(), asyncio.Event()
    async def request(method, url, **kwargs):
        if url.endswith("/token"):
            started.set()
            await release.wait()
        return await old_request(method, url, **kwargs)
    monkeypatch.setattr(oauth, "request", request)
    async with db_sessionmaker() as session:
        model = await session.get(McpServer, server["id"])
    refresh = asyncio.create_task(oauth.access_token(db_sessionmaker, model, force=True))
    await started.wait()
    await oauth.revoke(db_sessionmaker, model)
    release.set()
    with pytest.raises(ValueError, match="refresh"):
        await refresh
    async with db_sessionmaker() as session:
        assert await session.get(CodeMcpAuth, server["id"]) is None
