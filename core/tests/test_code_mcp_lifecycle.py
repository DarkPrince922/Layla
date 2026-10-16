import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from mcp import types

from app.models.mcp import McpServer
from app.services import code_mcp, code_mcp_oauth, mcp_client
from tests.test_code_runtime import setup_code


async def test_real_stdio_session_persists_state_and_binary_download_is_owned(client, db_sessionmaker):
    owner, _, chat = await setup_code(client)
    path = Path(__file__).parent / "fixtures/code_mcp.py"
    response = await client.post("/api/mcp/servers", json={"name": "Code fixture", "transport": "stdio",
        "command": f"{sys.executable} {path}"})
    assert response.status_code == 201, response.text
    server_id = response.json()["id"]
    async with db_sessionmaker() as session:
        server = await session.get(McpServer, server_id)
    agent = await code_mcp.McpAgent.create([server], maker=db_sessionmaker, owner_id=owner, chat_id=chat["id"])
    try:
        found = await agent.call("mcp_search_tools", {"query": "increment"})
        name = found["tools"][0]["name"]
        assert (await agent.call(name, {}))["count"] == 1
        assert (await agent.call(name, {}))["count"] == 2
        await agent.aclose()
        resumed = await code_mcp.McpAgent.create([server], maker=db_sessionmaker, owner_id=owner, chat_id=chat["id"])
        assert (await resumed.call(name, {}))["count"] == 3
        await resumed.aclose()
        result = await agent.call("mcp_read_resource", {"server": server_id, "uri": "test://binary"})
        attachment = result["code_attachments"][0]
        assert "data" not in attachment
        download = await client.get(attachment["url"])
        assert download.status_code == 200 and download.content == b"Code MCP binary result"
        assert download.headers["content-disposition"].startswith("attachment")
        async with db_sessionmaker() as session:
            row = await session.get(McpServer, server_id)
            row.enabled = False
            await session.commit()
        with pytest.raises(ValueError, match="недоступен"):
            await agent.call(name, {})
        await client.post("/api/auth/register", json={"email": "mcp-files-other@example.com", "password": "hunter2hunter2"})
        assert (await client.get(attachment["url"])).status_code == 404
    finally:
        await agent.aclose()
        await code_mcp.close_all()
    assert all(c.task is None for c in agent.connections.values())


async def test_connection_same_task_cleanup_no_mutating_replay_and_later_reconnect(monkeypatch):
    entered, exited, writes = [], [], []
    async def call_tool(**kwargs):
        writes.append(kwargs)
        raise RuntimeError("SERVER_SECRET must not escape")
    async def list_tools(**kwargs):
        return types.ListToolsResult(tools=[])
    @asynccontextmanager
    async def session(**kwargs):
        entered.append(asyncio.current_task())
        try:
            yield SimpleNamespace(call_tool=call_tool, list_tools=list_tools)
        finally:
            exited.append(asyncio.current_task())
    monkeypatch.setattr(mcp_client, "_session", session)
    connection = mcp_client.Connection(transport="stdio", command="fixture", url=None)
    with pytest.raises(RuntimeError, match="результат действия неизвестен") as failed:
        await connection.request("call_tool", name="write", arguments={})
    assert "SERVER_SECRET" not in str(failed.value) and len(writes) == 1
    await connection.request("list_tools")
    await connection.close()
    assert entered == exited and len(entered) == 2 and len(writes) == 1


async def test_cancellation_closes_connection_and_notifications_are_bounded(monkeypatch):
    started, closed = asyncio.Event(), asyncio.Event()
    async def call_tool(**kwargs):
        started.set()
        await asyncio.Event().wait()
    @asynccontextmanager
    async def session(**kwargs):
        try:
            yield SimpleNamespace(call_tool=call_tool)
        finally:
            closed.set()
    monkeypatch.setattr(mcp_client, "_session", session)
    connection = mcp_client.Connection(transport="stdio", command="fixture", url=None)
    for _ in range(40):
        await connection.notify(types.JSONRPCNotification(jsonrpc="2.0", method="notifications/tools/list_changed"))
    assert len(connection.notifications) == 30 and connection.dirty
    task = asyncio.create_task(connection.request("call_tool", name="wait"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_bearer_refreshes_once_on_401_and_never_follows_other_resource(client, db_sessionmaker, monkeypatch):
    owner, _, _ = await setup_code(client)
    server = McpServer(id="transport-test", owner_id=owner, url="https://mcp.example/mcp")
    refreshed, sent = [], []
    async def token(_maker, _server, force=False):
        refreshed.append(force)
        return "new-token" if force else "old-token"
    def handle(request):
        sent.append(request.headers["authorization"])
        return httpx.Response(401 if len(sent) == 1 else 200)
    monkeypatch.setattr(code_mcp_oauth, "access_token", token)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), auth=code_mcp_oauth.BearerAuth(db_sessionmaker, server)) as session:
        assert (await session.get(server.url)).status_code == 200
        with pytest.raises(ValueError, match="другому ресурсу"):
            await session.get("https://evil.example/mcp")
    assert refreshed == [False, True] and sent == ["Bearer old-token", "Bearer new-token"]


async def test_oauth_network_rejects_private_dns_and_pins_public_dns(monkeypatch):
    async def private(*args, **kwargs):
        return [(None, None, None, None, ("127.0.0.1", 443))]
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", private)
    with pytest.raises(ValueError, match="служебный адрес"):
        await code_mcp_oauth.request("GET", "https://metadata.example/config")
    async def public(*args, **kwargs):
        return [(None, None, None, None, ("93.184.216.34", 443))]
    monkeypatch.setattr(loop, "getaddrinfo", public)
    real = httpx.AsyncClient
    def handle(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "metadata.example"
        assert request.extensions["sni_hostname"] == "metadata.example"
        return httpx.Response(200, json={"issuer": "https://metadata.example"})
    monkeypatch.setattr(code_mcp_oauth.httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handle), **kwargs))
    status, _, value = await code_mcp_oauth.request("GET", "https://metadata.example/config")
    assert status == 200 and value["issuer"] == "https://metadata.example"
