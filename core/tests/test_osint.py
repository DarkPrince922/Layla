from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditLog
from app.models.mcp import IntelKey, McpServer
from app.models.osint import OsintArtifact, OsintLookup
from app.security import crypto
from app.services import mcp_client


async def register(client, email="osint@example.com"):
    response = await client.post(
        "/api/auth/register", json={"email": email, "password": "long-test-password"}
    )
    assert response.status_code == 201
    return response.json()


async def case(client, **kw):
    response = await client.post("/api/osint/cases", json={"subject": "EXAMPLE.COM.", **kw})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("path", ["/api/osint/cases", "/api/integrations/intelligence"])
async def test_requires_auth(client, path):
    assert (await client.get(path)).status_code == 401


async def test_case_lifecycle_sources_dedupe_and_audit(client, db_sessionmaker):
    await register(client)
    item = await case(client)
    assert item["subject"] == "example.com"
    await case(client, subject_type="person", subject="Public figure")
    await case(client, subject_type="company", subject="Example company")
    cases = (await client.get("/api/osint/cases?q=EXAMPLE&subject_type=domain")).json()
    assert [c["id"] for c in cases] == [item["id"]]
    assert (await client.get("/api/osint/cases?q=%25")).json() == []
    assert len((await client.get("/api/osint/cases?limit=1&offset=1")).json()) == 1
    path = f"/api/osint/cases/{item['id']}"
    note = {
        "title": "Observation",
        "summary": "Public documentation",
        "source_url": "https://example.com/docs",
    }
    first = await client.post(f"{path}/artifacts", json=note)
    second = await client.post(f"{path}/artifacts", json=note)
    assert first.status_code == 201 and second.json()["id"] == first.json()["id"]
    assert (await client.get(path)).json()["artifact_count"] == 1
    assert (await client.get(f"{path}/sources")).json() == [
        {"provider": "manual", "source_url": note["source_url"], "artifact_count": 1}
    ]
    bad = await client.post(f"{path}/artifacts", json={**note, "source_url": "javascript:alert(1)"})
    assert bad.status_code == 422
    assert (await client.delete(path)).status_code == 204
    assert (await client.get(path)).status_code == 404
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count(OsintArtifact.id))) == 0
        actions = list(await session.scalars(select(AuditLog.action)))
        assert {"osint.case_create", "osint.artifact_add", "osint.case_delete"} <= set(actions)


async def test_keys_encrypted_masked_replaced_and_deleted(client, db_sessionmaker):
    await register(client)
    path = "/api/integrations/intelligence/shodan"
    secret = "shodan-test-secret"
    for value in [secret, "replacement-secret"]:
        response = await client.put(path, json={"api_key": value})
        assert response.status_code == 200 and value not in response.text
        assert value not in (await client.get("/api/integrations/intelligence")).text
    async with db_sessionmaker() as session:
        rows = list(await session.scalars(select(IntelKey)))
        assert len(rows) == 1
        assert crypto.decrypt(rows[0].secret_ref) == "replacement-secret"
        assert "replacement-secret" not in rows[0].secret_ref
        audit = list(await session.scalars(select(AuditLog)))
        assert "replacement-secret" not in json.dumps([a.meta for a in audit])
    invalid = await client.put(path, json={"api_key": "secret\ninvalid"})
    assert invalid.status_code == 422 and "secret" not in invalid.text
    assert (await client.delete(path)).status_code == 204
    assert (
        await client.put("/api/integrations/intelligence/unknown", json={"api_key": secret})
    ).status_code == 422


async def test_owner_isolation_for_every_case_endpoint_and_keys(
    client, db_sessionmaker, monkeypatch
):
    await register(client)
    item = await case(client)
    await client.put("/api/integrations/intelligence/shodan", json={"api_key": "alice-secret"})
    await register(client, "bob@example.com")
    path = f"/api/osint/cases/{item['id']}"
    assert (await client.get("/api/osint/cases")).json() == []
    for suffix in ["", "/artifacts", "/sources", "/lookups"]:
        assert (await client.get(path + suffix)).status_code == 404
    assert (await client.delete(path)).status_code == 404
    assert (
        await client.post(
            path + "/artifacts",
            json={"title": "t", "summary": "s", "source_url": "https://example.com"},
        )
    ).status_code == 404

    async def no_call(**_):
        pytest.fail("Cross-owner lookup reached MCP")

    monkeypatch.setattr(mcp_client, "call_tool", no_call)
    assert (await client.post(path + "/lookups", json={"provider": "urlscan"})).status_code == 404
    assert not any(
        p["configured"] for p in (await client.get("/api/integrations/intelligence")).json()
    )
    await client.delete("/api/integrations/intelligence/shodan")
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count(IntelKey.id))) == 1


async def test_lookup_goes_through_mcp_and_persists_deduplicated_results(
    client, db_sessionmaker, monkeypatch
):
    await register(client)
    item = await case(client)
    await client.put("/api/integrations/intelligence/shodan", json={"api_key": "alice-secret"})
    calls = []

    async def fake_mcp(**kw):
        calls.append(kw)
        assert kw["name"] == "shodan_lookup" and kw["transport"] == "stdio"
        assert kw["arguments"] == {"target": "example.com"}
        assert kw["env"] == {
            "LAYLA_INTEL_PROVIDER": "shodan",
            "LAYLA_INTEL_API_KEY": "alice-secret",
        }
        assert "alice-secret" not in kw["command"]
        return {
            "status": "ok",
            "artifacts": [
                {
                    "title": "DNS",
                    "summary": "DNS record",
                    "source_url": "https://api.shodan.io/dns/domain/example.com",
                    "kind": "dns",
                    "data": {"domain": "example.com"},
                }
            ],
        }

    monkeypatch.setattr(mcp_client, "call_tool", fake_mcp)
    path = f"/api/osint/cases/{item['id']}"
    for added, dupes in [(1, 0), (0, 1)]:
        response = await client.post(path + "/lookups", json={"provider": "shodan"})
        assert response.status_code == 201, response.text
        assert (
            response.json()["artifact_count"] == added
            and response.json()["duplicate_count"] == dupes
        )
    summary = (await client.get(path)).json()
    assert summary["artifact_count"] == 1 and summary["lookup_count"] == 2
    assert len((await client.get(path + "/lookups")).json()) == 2
    assert "alice-secret" not in (await client.get(path + "/artifacts")).text
    assert len(calls) == 2
    await client.delete(path)
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count(OsintLookup.id))) == 0


async def test_failures_empty_results_validation_and_person_cases(client, monkeypatch):
    await register(client)
    item = await case(client, subject_type="person", subject="Public name")
    path = f"/api/osint/cases/{item['id']}/lookups"
    assert (await client.post(path, json={"provider": "urlscan"})).status_code == 422
    for payload in [
        {"provider": "unknown"},
        {"provider": "urlscan", "target": "https://target.com/"},
        {"provider": "urlscan", "target": "example.com", "active": True},
    ]:
        assert (await client.post(path, json=payload)).status_code == 422
    response = await client.post(path, json={"provider": "shodan", "target": "example.com"})
    assert response.json()["error_code"] == "not_configured"

    async def failed(**_):
        raise RuntimeError("upstream secret-key")

    monkeypatch.setattr(mcp_client, "call_tool", failed)
    response = await client.post(path, json={"provider": "urlscan", "target": "example.com"})
    assert response.json()["error_code"] == "mcp_error" and "secret-key" not in response.text

    async def empty(**_):
        return {"status": "empty", "artifacts": []}

    monkeypatch.setattr(mcp_client, "call_tool", empty)
    assert (await client.post(path, json={"provider": "urlscan", "target": "example.com"})).json()[
        "status"
    ] == "empty"


async def test_mcp_env_is_encrypted_and_decrypted_only_for_execution(
    client, db_sessionmaker, monkeypatch
):
    await register(client)
    created = (
        await client.post(
            "/api/mcp/servers",
            json={"name": "test", "command": "worker", "env": {"TOKEN": "mcp-secret"}},
        )
    ).json()
    assert created["env_keys"] == ["TOKEN"]
    async with db_sessionmaker() as session:
        row = await session.get(McpServer, created["id"])
        assert row.env == {} and "mcp-secret" not in row.env_secret_ref
        assert json.loads(crypto.decrypt(row.env_secret_ref)) == {"TOKEN": "mcp-secret"}

    async def fake(**kw):
        assert kw["env"] == {"TOKEN": "mcp-secret"}
        return []

    monkeypatch.setattr(mcp_client, "list_tools", fake)
    assert (await client.post(f"/api/mcp/servers/{created['id']}/test")).json()["ok"]
    await client.patch(f"/api/mcp/servers/{created['id']}", json={"enabled": False})
    assert not (await client.post(f"/api/mcp/servers/{created['id']}/test")).json()["ok"]
