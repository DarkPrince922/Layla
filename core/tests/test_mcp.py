"""Тесты реестра MCP и проверки подключения (спец. §5.8)."""
from __future__ import annotations

import pytest

import app.services.mcp_client as mcp_client


async def _register(client):
    await client.post(
        "/api/auth/register",
        json={"email": "mcp@example.com", "password": "hunter2hunter2"},
    )


@pytest.mark.asyncio
async def test_register_and_list_mcp_server(client):
    await _register(client)
    r = await client.post(
        "/api/mcp/servers",
        json={
            "name": "filesystem",
            "transport": "stdio",
            "command": "npx -y @modelcontextprotocol/server-filesystem /tmp",
            "personas": ["p1"],
            "env": {"TOKEN": "secret-value"},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "filesystem"
    # Значения env не возвращаются — только имена переменных.
    assert body["env_keys"] == ["TOKEN"]
    assert "secret-value" not in r.text

    lst = (await client.get("/api/mcp/servers")).json()
    assert len(lst) == 1


@pytest.mark.asyncio
async def test_toggle_and_delete(client):
    await _register(client)
    srv = (
        await client.post(
            "/api/mcp/servers", json={"name": "s", "transport": "http", "url": "http://x/mcp"}
        )
    ).json()
    upd = (
        await client.patch(f"/api/mcp/servers/{srv['id']}", json={"enabled": False})
    ).json()
    assert upd["enabled"] is False
    r = await client.delete(f"/api/mcp/servers/{srv['id']}")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_test_connection_uses_client(client, monkeypatch):
    await _register(client)
    srv = (
        await client.post(
            "/api/mcp/servers",
            json={"name": "s", "transport": "stdio", "command": "run"},
        )
    ).json()

    async def fake_list_tools(**kw):
        return [{"name": "read_file", "description": "Read a file"}]

    monkeypatch.setattr(mcp_client, "list_tools", fake_list_tools)
    res = (await client.post(f"/api/mcp/servers/{srv['id']}/test")).json()
    assert res["ok"] is True
    assert res["tools"][0]["name"] == "read_file"


@pytest.mark.asyncio
async def test_test_connection_reports_error(client, monkeypatch):
    await _register(client)
    srv = (
        await client.post(
            "/api/mcp/servers", json={"name": "s", "transport": "stdio", "command": "run"}
        )
    ).json()

    async def fake_list_tools(**kw):
        raise RuntimeError("SDK не установлен")

    monkeypatch.setattr(mcp_client, "list_tools", fake_list_tools)
    res = (await client.post(f"/api/mcp/servers/{srv['id']}/test")).json()
    assert res["ok"] is False
    assert "SDK" in res["error"]
