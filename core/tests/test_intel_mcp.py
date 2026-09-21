from __future__ import annotations

import asyncio
import shlex
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP

from app.services import intelligence, mcp_client


async def test_real_bundled_mcp_tools_and_safe_errors():
    tools = await mcp_client.list_tools(
        transport="stdio", command=intelligence.MCP_COMMAND, url=None
    )
    assert {t["name"] for t in tools} == {
        "shodan_lookup",
        "virustotal_lookup",
        "securitytrails_lookup",
        "urlscan_lookup",
    }
    result = await mcp_client.call_tool(
        transport="stdio",
        command=intelligence.MCP_COMMAND,
        name="urlscan_lookup",
        arguments={"target": "http://127.0.0.1/internal"},
        env={"LAYLA_INTEL_PROVIDER": "urlscan"},
    )
    assert result["error_code"] == "invalid_target"
    # A provider's secret must not be available to tools for another provider.
    result = await mcp_client.call_tool(
        transport="stdio",
        command=intelligence.MCP_COMMAND,
        name="shodan_lookup",
        arguments={"target": "example.com"},
        env={"LAYLA_INTEL_PROVIDER": "urlscan", "LAYLA_INTEL_API_KEY": "other-secret"},
    )
    assert result["error_code"] == "not_configured"
    assert "other-secret" not in str(result)


async def test_api_to_real_mcp_to_persisted_artifact(client, monkeypatch):
    fixture = Path(__file__).parent / "fixtures/intelligence_mcp.py"
    monkeypatch.setattr(
        intelligence, "MCP_COMMAND", f"{shlex.quote(sys.executable)} {shlex.quote(str(fixture))}"
    )
    await client.post(
        "/api/auth/register",
        json={"email": "real-mcp@example.com", "password": "a-long-test-password"},
    )
    item = (await client.post("/api/osint/cases", json={"subject": "example.com"})).json()
    response = await client.post(
        f"/api/osint/cases/{item['id']}/lookups", json={"provider": "urlscan"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "ok", response.text
    artifacts = (await client.get(f"/api/osint/cases/{item['id']}/artifacts")).json()
    assert len(artifacts) == 1
    assert artifacts[0]["title"] == "Example Domain"
    assert (
        artifacts[0]["source_url"]
        == "https://urlscan.io/result/8848d620-00b1-4c0a-a46a-83d369529487/"
    )


async def test_real_streamable_http_transport(monkeypatch):
    server = FastMCP("test", stateless_http=True, json_response=True)

    @server.tool()
    async def hello() -> dict:
        return {"message": "hello"}

    app = server.streamable_http_app()

    @asynccontextmanager
    async def transport(url):
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client,
            streamable_http_client(url, http_client=client) as streams,
        ):
            yield streams

    monkeypatch.setattr(mcp_client, "streamable_http_client", transport)
    async with server.session_manager.run():
        tools = await mcp_client.list_tools(
            transport="http", command=None, url="http://localhost:8000/mcp"
        )
        assert [t["name"] for t in tools] == ["hello"]
        result = await mcp_client.call_tool(
            transport="http", url="http://localhost:8000/mcp", name="hello", arguments={}
        )
        assert result == {"message": "hello"}


async def test_mcp_failure_and_deadline_are_bounded():
    with pytest.raises(RuntimeError, match="подключиться"):
        await mcp_client.list_tools(transport="stdio", command="/no/such/secret-worker", url=None)
    # The deadline also terminates a child that never starts MCP.
    command = f"{shlex.quote(sys.executable)} -c 'import time; time.sleep(60)'"
    async with asyncio.timeout(8):
        with pytest.raises(RuntimeError):
            await mcp_client.list_tools(transport="stdio", command=command, url=None, timeout=0.3)
