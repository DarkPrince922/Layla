"""Тесты API агента: планирование, HITL-гейт, триаж, настройки (спец. §5.2, §7.8)."""
from __future__ import annotations

import pytest

import app.services.provider_client as pc

_PLAN = """[
  {"role":"explorer","kind":"analysis","target":"example.com","summary":"пассивная разведка"},
  {"role":"implementer","kind":"command","target":"example.com","command":"nmap -sV example.com","summary":"скан портов"},
  {"role":"implementer","kind":"command","target":"evil.com","command":"nmap evil.com","summary":"вне scope"}
]"""


async def _setup(client, monkeypatch, *, authorize=True, venue="this_machine"):
    await client.post(
        "/api/auth/register", json={"email": "ag@example.com", "password": "hunter2hunter2"}
    )
    await client.post(
        "/api/providers",
        json={"name": "M", "kind": "openai_compatible", "base_url": "http://p/v1",
              "default_model": "gpt-4o", "active": True},
    )
    e = (await client.post("/api/engagements", json={"target": "example.com"})).json()
    eid = e["id"]
    await client.put(f"/api/engagements/{eid}/scope", json={"allow": ["example.com"], "deny": []})
    await client.post(f"/api/engagements/{eid}/scope/confirm")
    if authorize:
        await client.post(f"/api/engagements/{eid}/authorize")
        await client.put(f"/api/engagements/{eid}/venue", json={"mode": venue, "egress_route": "direct"})

    async def fake_complete(provider, key, model, messages, **kw):
        return _PLAN

    monkeypatch.setattr(pc, "complete", fake_complete)
    return eid


@pytest.mark.asyncio
async def test_create_run_plans_steps_with_hitl(client, monkeypatch):
    eid = await _setup(client, monkeypatch)
    run = (
        await client.post(
            f"/api/engagements/{eid}/agent/runs",
            json={"task": "проверить веб", "mode": "interactive"},
        )
    ).json()
    kinds = {s["kind"] for s in run["steps"]}
    assert "analysis" in kinds and "command" in kinds
    cmd_steps = [s for s in run["steps"] if s["kind"] == "command"]
    # В interactive-режиме командные шаги ждут подтверждения оператора.
    assert all(s["status"] == "awaiting_approval" for s in cmd_steps)
    assert all(s["requires_hitl"] for s in cmd_steps)


@pytest.mark.asyncio
async def test_approve_in_scope_command_gated_no_executor(client, monkeypatch):
    eid = await _setup(client, monkeypatch)
    run = (
        await client.post(f"/api/engagements/{eid}/agent/runs",
                          json={"task": "t", "mode": "interactive"})
    ).json()
    in_scope = next(s for s in run["steps"] if s["command"] and "example.com" in s["command"])
    res = (await client.post(f"/api/agent/steps/{in_scope['id']}/approve")).json()
    # Гейты пройдены (в scope, authorized), но реальный исполнитель не подключён.
    assert res["status"] == "blocked"
    assert "исполнитель" in res["output"].lower()


@pytest.mark.asyncio
async def test_approve_out_of_scope_command_blocked_by_gate(client, monkeypatch):
    eid = await _setup(client, monkeypatch)
    run = (
        await client.post(f"/api/engagements/{eid}/agent/runs",
                          json={"task": "t", "mode": "interactive"})
    ).json()
    out = next(s for s in run["steps"] if s["command"] and "evil.com" in s["command"])
    res = (await client.post(f"/api/agent/steps/{out['id']}/approve")).json()
    assert res["status"] == "blocked"
    assert "scope" in res["output"].lower()


@pytest.mark.asyncio
async def test_command_blocked_when_analysis_only(client, monkeypatch):
    # Без авторизации venue остаётся analysis_only → активные команды блокируются.
    eid = await _setup(client, monkeypatch, authorize=False)
    run = (
        await client.post(f"/api/engagements/{eid}/agent/runs",
                          json={"task": "t", "mode": "interactive"})
    ).json()
    cmd = next(s for s in run["steps"] if s["command"] and "example.com" in s["command"])
    res = (await client.post(f"/api/agent/steps/{cmd['id']}/approve")).json()
    assert res["status"] == "blocked"


@pytest.mark.asyncio
async def test_deny_step(client, monkeypatch):
    eid = await _setup(client, monkeypatch)
    run = (
        await client.post(f"/api/engagements/{eid}/agent/runs",
                          json={"task": "t", "mode": "interactive"})
    ).json()
    cmd = next(s for s in run["steps"] if s["kind"] == "command")
    res = (await client.post(f"/api/agent/steps/{cmd['id']}/deny")).json()
    assert res["status"] == "denied"


@pytest.mark.asyncio
async def test_triage_finding(client, monkeypatch):
    eid = await _setup(client, monkeypatch)
    f = (await client.post(f"/api/engagements/{eid}/findings",
                           json={"severity": "HIGH", "type": "SQLi", "url": "https://example.com/a"})).json()

    async def fake_complete(provider, key, model, messages, **kw):
        return "Вероятно истинное срабатывание. Проверить вручную параметр."

    monkeypatch.setattr(pc, "complete", fake_complete)
    res = (await client.post(f"/api/engagements/{eid}/findings/{f['id']}/triage", json={})).json()
    assert res["finding_id"] == f["id"]
    assert "срабатыв" in res["verdict"].lower()


@pytest.mark.asyncio
async def test_agent_config_roundtrip(client):
    await client.post(
        "/api/auth/register", json={"email": "cfg@example.com", "password": "hunter2hunter2"}
    )
    await client.put("/api/agent/config", json={
        "preset": "constellation",
        "role_models": {"explorer": "gpt-4o", "reviewer": "inherit"},
        "budgets": {"tokens_per_turn": 5000, "cost_usd": 2},
        "diagnostics": {"command": "npm run typecheck", "run_after_edits": True},
        "context": {},
    })
    cfg = (await client.get("/api/agent/config")).json()
    assert cfg["preset"] == "constellation"
    assert cfg["budgets"]["tokens_per_turn"] == 5000
