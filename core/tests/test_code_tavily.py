"""Existing encrypted integration keys -> Code Tavily HTTP -> shared budget."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.models.code_control import CodeUsageCall
from app.models.enums import IntelProvider
from app.models.mcp import IntelKey
from app.security import crypto
from app.services import code_tavily, code_usage, code_web_tools, project_agent
from tests.test_code_workers import setup

REAL_CLIENT = httpx.AsyncClient
SECRET = "tvly-test-owner-secret"


@pytest.fixture
def transport(monkeypatch):
    calls, handler = [], None
    def route(request):
        calls.append(request)
        return handler(request)
    def install(answer):
        nonlocal handler
        handler = answer
        return calls
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(route), **kwargs))
    return install


def answer(**extra):
    return {"results": [{"title": "Docs", "url": "https://docs.example/guide", "content": "Use run()"},
        {"title": "Other", "url": "https://elsewhere.example", "content": "excluded"}],
        "usage": {"credits": 1}, "request_id": "test-request", **extra}


async def prepare(client, maker):
    manager, chat, root = await setup(client, maker)
    saved = await client.put("/api/integrations/intelligence/tavily", json={"api_key": SECRET})
    assert saved.status_code == 200, saved.text
    return manager, chat, root


async def ledger(maker, chat_id):
    async with maker() as session:
        return list(await session.scalars(select(CodeUsageCall).where(CodeUsageCall.chat_id == chat_id)))


async def test_uses_existing_integration_key_api_and_filters_without_model_provider(client, db_sessionmaker, transport):
    manager, chat, root = await prepare(client, db_sessionmaker)
    async with db_sessionmaker() as session:
        key = await session.scalar(select(IntelKey).where(IntelKey.owner_id == manager.runtime.owner_id))
        assert key.provider == IntelProvider.tavily and key.secret_ref != SECRET and crypto.decrypt(key.secret_ref) == SECRET
    listed = (await client.get("/api/integrations/intelligence?include_code=true")).json()
    assert any(row["provider"] == "tavily" and row["configured"] for row in listed)
    assert SECRET not in json.dumps(listed)
    assert not any(row["provider"] == "tavily" for row in (await client.get("/api/integrations/intelligence")).json())
    calls = transport(lambda request: httpx.Response(200, json=answer()))
    async with code_usage.tracking(manager.runtime):
        result = await code_web_tools.Runtime(str(root)).call("web_search", {"query": "docs", "allowed_domains": ["docs.example"]},
            SimpleNamespace(kind="openai_compatible"), "unrelated-model-key", "any-model", {})
    request = calls[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.tavily.com/search"
    assert request.headers["authorization"] == "Bearer " + SECRET
    assert "unrelated-model-key" not in str(request.headers) and "api_key" not in body
    assert body["include_domains"] == ["docs.example"] and body["include_domains_mode"] == "restrict"
    assert body["search_depth"] == "basic" and body["auto_parameters"] is False and body["include_usage"] is True
    assert len(result["sources"]) == 1 and result["results"][0]["content"][0]["content"] == "Use run()"
    assert result["backend"] == "tavily" and SECRET not in json.dumps(result)
    row, = await ledger(db_sessionmaker, chat["id"])
    assert row.charged_tokens == 0 and row.charged_cost == 8000 and row.status == "reported"


async def test_no_key_no_network_and_owner_keys_are_isolated(client, db_sessionmaker, transport):
    manager, _chat, _root = await setup(client, db_sessionmaker)
    calls = transport(lambda request: (_ for _ in ()).throw(AssertionError("No HTTP")))
    await client.post("/api/auth/register", json={"email": "other-tavily@example.com", "password": "hunter2hunter2"})
    await client.put("/api/integrations/intelligence/tavily", json={"api_key": "tvly-other"})
    async with code_usage.tracking(manager.runtime):
        with pytest.raises(ValueError, match="Интеграции"):
            await code_tavily.search(code_web_tools.WebSearch(query="docs"))
    assert not calls and not await ledger(db_sessionmaker, manager.runtime.chat_id)


async def test_updated_and_deleted_integration_key_is_observed_per_call(client, db_sessionmaker, transport):
    manager, _chat, _root = await prepare(client, db_sessionmaker)
    calls = transport(lambda request: httpx.Response(200, json=answer()))
    async with code_usage.tracking(manager.runtime):
        await code_tavily.search(code_web_tools.WebSearch(query="docs", blocked_domains=["elsewhere.example"]))
        await client.put("/api/integrations/intelligence/tavily", json={"api_key": "tvly-replaced"})
        await code_tavily.search(code_web_tools.WebSearch(query="docs"))
        await client.delete("/api/integrations/intelligence/tavily")
        with pytest.raises(ValueError, match="Интеграции"):
            await code_tavily.search(code_web_tools.WebSearch(query="docs"))
    assert [request.headers["authorization"] for request in calls] == ["Bearer " + SECRET, "Bearer tvly-replaced"]
    assert json.loads(calls[0].content)["exclude_domains"] == ["elsewhere.example"]


async def test_budget_reserves_search_fee_without_llm_tokens_and_blocks_before_http(client, db_sessionmaker, transport):
    manager, chat, _root = await prepare(client, db_sessionmaker)
    calls = transport(lambda request: httpx.Response(200, json=answer()))
    await code_usage.configure(manager.runtime, code_usage.Policy(max_tokens=1, max_usd="0.004", tavily_usd_per_thousand="5"), None, None)
    async with code_usage.tracking(manager.runtime):
        with pytest.raises(code_usage.BudgetExceeded):
            await code_tavily.search(code_web_tools.WebSearch(query="docs"))
        assert not calls
        await code_usage.configure(manager.runtime, code_usage.Policy(max_tokens=1, max_usd="0.01"), None, None)
        await code_tavily.search(code_web_tools.WebSearch(query="docs"))
    row, = await ledger(db_sessionmaker, chat["id"])
    assert row.charged_tokens == 0 and row.charged_cost == 5000
    assert (await code_usage.snapshot(manager.runtime))["tavily_usd_per_thousand"] == "5"


@pytest.mark.parametrize("case,status,cost", [("401", "rejected", 0), ("500", "uncertain", 8000),
    ("timeout", "uncertain", 8000), ("cancel", "uncertain", 8000), ("invalid", "uncertain", 8000),
    ("missing_usage", "estimated", 8000)])
async def test_errors_cancel_and_missing_usage_are_safe_and_not_retried(case, status, cost, client, db_sessionmaker, transport):
    manager, chat, _root = await prepare(client, db_sessionmaker)
    def handle(request):
        if case.isdigit():
            return httpx.Response(int(case), text=SECRET)
        if case == "timeout":
            raise httpx.ReadTimeout(SECRET, request=request)
        if case == "cancel":
            raise asyncio.CancelledError
        if case == "invalid":
            return httpx.Response(200, content=b"not-json")
        return httpx.Response(200, json=answer(usage=None))
    calls = transport(handle)
    async with code_usage.tracking(manager.runtime):
        if case == "missing_usage":
            result = await code_tavily.search(code_web_tools.WebSearch(query="docs"))
            assert result["credits"] is None
        else:
            with pytest.raises(asyncio.CancelledError if case == "cancel" else ValueError) as error:
                await code_tavily.search(code_web_tools.WebSearch(query="docs"))
            assert SECRET not in str(error.value)
    assert len(calls) == 1
    row, = await ledger(db_sessionmaker, chat["id"])
    assert row.status == status and row.charged_cost == cost


async def test_agent_dispatch_search_with_openai_model_and_confirm(client, db_sessionmaker, monkeypatch, transport):
    manager, _chat, root = await prepare(client, db_sessionmaker)
    calls = transport(lambda request: httpx.Response(200, json=answer()))
    turns, approvals = 0, []
    async def turn(_p, _k, _m, messages, tools, _caps, anchor=None):
        nonlocal turns
        turns += 1
        if turns == 1:
            tool = next(t for t in tools if t["function"]["name"] == "web_search")
            assert "Tavily" in tool["function"]["description"]
            yield "done", ([], [], {}, [{"id": "s", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"docs"}'}}])
        else:
            result = json.loads(messages[-1]["content"])
            assert result["backend"] == "tavily" and result["sources"][0]["url"].startswith("https://")
            yield "done", (["Sources: https://docs.example/guide"], [], {}, [])
    async def approve(event):
        approvals.append(event)
        return "approve"
    monkeypatch.setattr(project_agent, "_turn", turn)
    async with code_usage.tracking(manager.runtime):
        events = [e async for e in project_agent.run(SimpleNamespace(kind="openai_compatible", models=[], model_caps={}),
            None, "m", [{"role": "user", "content": "Find docs"}], str(root), runtime=manager.runtime,
            mode="confirm", approve=approve)]
    assert len(calls) == len(approvals) == 1 and any(e.get("tool", {}).get("status") == "done" for e in events)
