"""Archive web contracts over real HTTP parsing, without external network access."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.models.code_control import CodeUsageCall
from app.services import (
    code_hooks,
    code_runner,
    code_skill_policy,
    code_usage,
    code_web_http,
    code_web_tools,
    project_agent,
    provider_client,
)
from tests.test_code_workers import setup

REAL_CLIENT = httpx.AsyncClient


def provider():
    return SimpleNamespace(id="p", kind="anthropic", base_url="https://provider.example", models=[], model_caps={})


def response(*, stop="end_turn", count=1, error=None):
    return {"stop_reason": stop, "usage": {"input_tokens": 100, "output_tokens": 20,
        "server_tool_use": {"web_search_requests": count}}, "content": [
        {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "q"}},
        {"type": "web_search_tool_result", "tool_use_id": "s1", "content":
         {"error_code": error} if error else [{"title": "Docs", "url": "https://docs.example/guide", "encrypted_content": "private-provider-field"}]},
        {"type": "text", "text": "Found official documentation."}]}


@pytest.fixture
def transport(monkeypatch):
    requests = []
    handler = None
    code_web_http.CACHE.clear()
    code_web_http.DOMAIN_CACHE.clear()
    async def public(_host):
        return ["1.1.1.1"]
    def routed(request):
        requests.append(request)
        return handler(request)
    def install(answer):
        nonlocal handler
        handler = answer
        return requests
    monkeypatch.setattr(code_web_http, "addresses", public)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(routed), **kwargs))
    return install


@pytest.mark.parametrize("value", ["http://127.0.0.1", "https://169.254.169.254", "https://[::1]", "https://u:p@example.com", "ftp://example.com", "https://localhost", "https://example.com:444"])
def test_archive_url_upgrade_and_private_address_rejection(value):
    assert code_web_http.url("http://Example.com/a?q=1#x") == "https://example.com/a?q=1"
    with pytest.raises(ValueError):
        code_web_http.url(value)


def test_archive_schemas_and_domain_rules():
    with pytest.raises(ValidationError):
        code_web_tools.WebSearch(query="x")
    with pytest.raises(ValidationError):
        code_web_tools.WebSearch(query="query", allowed_domains=["a.example"], blocked_domains=["b.example"])
    with pytest.raises(ValidationError):
        code_web_tools.WebFetch(url="https://example.com")
    assert code_web_http.preapproved("https://github.com/anthropics/project")
    assert not code_web_http.preapproved("https://github.com/anthropics-evil/project")
    rules = code_skill_policy.parse("WebFetch(domain:docs.example)")
    assert code_skill_policy.allows(rules, "web_fetch", {"url": "http://docs.example/a"})
    assert not code_skill_policy.allows(rules, "web_fetch", {"url": "https://evil.example"})


async def test_native_search_uses_archive_server_tool_filters_and_result_blocks(transport):
    requests = transport(lambda request: httpx.Response(200, json=response()))
    args = code_web_tools.WebSearch(query="current docs", allowed_domains=["docs.example"])
    result = await code_web_tools.native_search(args, provider(), "secret", "claude-test", {})
    body = json.loads(requests[0].content)
    assert body["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8, "allowed_domains": ["docs.example"]}]
    assert body["model"] == "claude-test" and requests[0].headers["x-api-key"] == "secret"
    assert result["results"][0]["content"][0]["title"] == "Docs"
    assert result["sources"][0]["url"] == "https://docs.example/guide"
    assert "encrypted_content" not in json.dumps(result)
    with pytest.raises(ValueError, match="Messages API"):
        await code_web_tools.native_search(args, SimpleNamespace(kind="openai_compatible"), None, "m", {})
    assert len(requests) == 1  # no disguised Bing fallback


async def test_native_search_rotation_server_errors_and_pause_continuation(transport):
    def handler(request):
        if request.headers["x-api-key"] == "bad":
            return httpx.Response(401, json={"error": {"message": "Invalid key"}})
        body = json.loads(request.content)
        return httpx.Response(200, json=response(stop="pause_turn" if len(body["messages"]) == 1 else "end_turn"))
    requests = transport(handler)
    ring = provider_client.KeyRing([("web-bad", "bad", None), ("web-good", "good", None)])
    result = await code_web_tools.native_search(code_web_tools.WebSearch(query="docs"), provider(), ring, "m", {})
    assert len(requests) == 3 and "error" not in result
    assert json.loads(requests[-1].content)["tools"][0]["max_uses"] == 7
    requests.clear()
    transport(lambda request: httpx.Response(200, json=response(count=0, error="max_uses_exceeded")))
    result = await code_web_tools.native_search(code_web_tools.WebSearch(query="docs"), provider(), None, "m", {})
    assert "max_uses_exceeded" in result["error"] and len(requests) == 1


async def test_fetch_markdown_prompt_cache_and_scope_isolation(tmp_path, monkeypatch, transport):
    def handler(request):
        host = request.headers["host"]
        assert request.url.host == "1.1.1.1" and "authorization" not in request.headers and "x-api-key" not in request.headers
        if host == "api.anthropic.com":
            return httpx.Response(200, json={"can_fetch": True})
        return httpx.Response(200, text="<h1>Guide</h1><p>Use <code>run()</code>.</p><script>HIDDEN_SCRIPT</script>", headers={"content-type": "text/html"})
    requests = transport(handler)
    prompts = []
    async def extract(_p, _k, _m, conversation, tools, caps):
        prompts.append(conversation[-1]["content"])
        assert not tools and "Guide\n=====" in prompts[-1] and "HIDDEN_SCRIPT" not in prompts[-1]
        yield "done", (["Extracted run()"], [], {}, [])
    monkeypatch.setattr(project_agent, "_turn", extract)
    runtime = code_web_tools.Runtime(str(tmp_path), scope="owner")
    args = {"url": "http://docs.example/a", "prompt": "Find the function"}
    first = await runtime.call("web_fetch", args, provider(), "provider-secret", "m", {})
    second = await runtime.call("web_fetch", {**args, "prompt": "Explain the function"}, provider(), "provider-secret", "m", {})
    assert first["result"] == "Extracted run()" and second["cached"] and len(prompts) == 2
    assert len(requests) == 2 and "Explain the function" in prompts[-1]
    await code_web_tools.Runtime(str(tmp_path), scope="other-owner").call("web_fetch", args, provider(), None, "m", {})
    assert len(requests) == 3


async def test_cross_host_redirect_requires_new_call_and_preapproved_markdown_skips_model(tmp_path, monkeypatch, transport):
    def handler(request):
        host = request.headers["host"]
        if host == "api.anthropic.com":
            return httpx.Response(200, json={"can_fetch": True})
        if host == "example.com":
            return httpx.Response(302, headers={"location": "https://other.example/new"})
        return httpx.Response(200, text="# Python docs", headers={"content-type": "text/markdown"})
    requests = transport(handler)
    runtime = code_web_tools.Runtime(str(tmp_path))
    result = await runtime.call("web_fetch", {"url": "https://example.com", "prompt": "Read"}, provider(), None, "m", {})
    assert result["type"] == "redirect" and result["redirectUrl"] == "https://other.example/new"
    assert not any(request.headers["host"] == "other.example" for request in requests)
    result = await runtime.call("web_fetch", {"url": "https://docs.python.org/a", "prompt": "Read"}, None, None, "m", {})
    assert result["result"] == "# Python docs"


async def test_preflight_failure_is_not_cached_and_skip_is_snapshot(tmp_path, transport):
    requests = transport(lambda request: httpx.Response(503))
    runtime = code_web_tools.Runtime(str(tmp_path))
    with pytest.raises(ValueError, match="сервис проверки"):
        await runtime.call("web_fetch", {"url": "https://example.com", "prompt": "Read"}, None, None, "m", {})
    assert not code_web_http.DOMAIN_CACHE and not code_web_http.CACHE
    directory = tmp_path / ".layla"
    directory.mkdir()
    (directory / "settings.json").write_text('{"skipWebFetchPreflight": true}')
    assert not runtime.skip_preflight
    assert code_web_tools.Runtime(str(tmp_path)).skip_preflight
    assert len(requests) == 1


async def test_search_budget_reserves_server_fee_and_reconciles_usage(client, db_sessionmaker):
    manager, _chat, _root = await setup(client, db_sessionmaker)
    await code_usage.configure(manager.runtime, code_usage.Policy(max_tokens=1000000, max_usd="1",
        input_usd_per_million="1", output_usd_per_million="2", web_search_usd_per_thousand="20"), manager.provider_id, "m")
    account = code_usage.Account(manager.runtime)
    call_id = await account.reserve(manager.provider_id, "m", [], [], {"context": 10000, "max_output": 100}, server_searches=8)
    await account.settle(call_id, {"input_tokens": 100, "output_tokens": 20, "web_search_requests": 2})
    async with db_sessionmaker() as session:
        row = await session.get(CodeUsageCall, call_id)
        assert row.charged_cost == 40140 and row.prices["searches"] == 2 and row.status == "reported"
    await code_usage.configure(manager.runtime, code_usage.Policy(max_usd="0.01", input_usd_per_million="0", output_usd_per_million="0"), manager.provider_id, "m")
    with pytest.raises(code_usage.BudgetExceeded):
        await account.reserve(manager.provider_id, "m", [], [], {}, server_searches=8)


async def test_code_tool_dispatch_uses_archive_contract_and_confirm_rejects_before_network(tmp_path, monkeypatch):
    requests, turns = [], 0
    async def forbidden(*args, **kwargs):
        requests.append(args)
        raise AssertionError("network must not run")
    monkeypatch.setattr(code_web_tools, "native_search", forbidden)
    async def turn(_p, _k, _m, messages, tools, _caps, anchor=None):
        nonlocal turns
        turns += 1
        spec = next(t for t in tools if t["function"]["name"] == "web_fetch")
        assert "prompt" in spec["function"]["parameters"]["required"]
        if turns == 1:
            calls = [{"id": "s", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"docs"}'}}]
            yield "done", ([], [], {}, calls)
        else:
            assert "отклонил" in messages[-1]["content"]
            yield "done", (["Denied"], [], {}, [])
    async def reject(_event):
        return "reject"
    monkeypatch.setattr(project_agent, "_turn", turn)
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    events = [item async for item in project_agent.run(provider(), None, "m", [{"role": "user", "content": "Find docs"}],
        str(tmp_path), worker_context=True, mode="confirm", approve=reject, workflow=code_hooks.Runtime(runner))]
    assert not requests and any(item.get("tool", {}).get("status") == "pending" for item in events)


async def test_mixed_public_private_dns_is_rejected(monkeypatch):
    async def resolve(*args, **kwargs):
        return [(2, 1, 6, "", (host, 443)) for host in ("1.1.1.1", "127.0.0.1")]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(ValueError, match="DNS"):
        await code_web_http.addresses("docs.example")


async def test_www_redirect_and_private_location_and_size_limit(transport, monkeypatch):
    def handler(request):
        if request.headers["host"] == "example.com":
            return httpx.Response(301, headers={"location": "https://www.example.com/docs"})
        return httpx.Response(200, text="public", headers={"content-type": "text/plain"})
    requests = transport(handler)
    result = await code_web_http.fetch("https://example.com", "owner", skip_preflight=True)
    assert result["url"] == "https://www.example.com/docs" and len(requests) == 2
    transport(lambda request: httpx.Response(302, headers={"location": "http://127.0.0.1/secret"}))
    with pytest.raises(ValueError):
        await code_web_http.fetch("https://example.com/private", "owner", skip_preflight=True)
    assert len(requests) == 3
    transport(lambda request: httpx.Response(200, content=b"12345"))
    with pytest.raises(ValueError, match="размера"):
        await code_web_http.get("https://example.com/large", limit=4)


async def test_fetch_uses_enabled_haiku_model_with_own_caps(tmp_path, monkeypatch, transport):
    transport(lambda request: httpx.Response(200, text="Information", headers={"content-type": "text/plain"}))
    p = provider()
    p.models = [{"name": "haiku-disabled", "enabled": False}, {"name": "claude-haiku", "enabled": True}]
    p.model_caps = {"claude-haiku": {"max_output": 512}}
    async def extract(_p, _k, model, messages, tools, caps):
        assert model == "claude-haiku" and caps == {"max_output": 512} and not tools
        yield "done", (["Summary"], [], {}, [])
    monkeypatch.setattr(project_agent, "_turn", extract)
    runtime = code_web_tools.Runtime(str(tmp_path))
    runtime.skip_preflight = True
    result = await runtime.call("web_fetch", {"url": "https://example.com", "prompt": "Summarize"}, p, None, "main", {})
    assert result["processing_model"] == "claude-haiku"


async def test_binary_fetch_creates_owned_download(client, db_sessionmaker, transport):
    manager, _chat, root = await setup(client, db_sessionmaker)
    transport(lambda request: httpx.Response(200, content=b"%PDF-test", headers={"content-type": "application/pdf"}))
    runtime = code_web_tools.Runtime(str(root))
    runtime.skip_preflight = True
    token = code_usage.ACTIVE.set(code_usage.Account(manager.runtime))
    try:
        result = await runtime.call("web_fetch", {"url": "https://example.com/a.pdf", "prompt": "Read"}, provider(), None, "m", {})
    finally:
        code_usage.ACTIVE.reset(token)
    attachment = result["code_attachments"][0]
    from app.models.code_mcp import CodeMcpArtifact
    async with db_sessionmaker() as session:
        saved = await session.get(CodeMcpArtifact, attachment["id"])
        assert saved.owner_id == manager.runtime.owner_id and saved.chat_id == manager.runtime.chat_id
        assert saved.data == b"%PDF-test"
    assert "not performed" in result["result"]


async def test_search_missing_usage_keeps_reserve_and_rejection_releases_it(client, db_sessionmaker, transport):
    manager, _chat, _root = await setup(client, db_sessionmaker)
    await code_usage.configure(manager.runtime, code_usage.Policy(input_usd_per_million="1", output_usd_per_million="2"), "p", "m")
    await code_usage.configure(manager.runtime, code_usage.Policy(web_search_usd_per_thousand="15"), "p", "m")
    assert (await code_usage.snapshot(manager.runtime, "p", "m"))["prices"]["web_search"] == "15"
    account = code_usage.Account(manager.runtime)
    token = code_usage.ACTIVE.set(account)
    data = response()
    data["usage"]["server_tool_use"] = "malformed"
    transport(lambda request: httpx.Response(200, json=data))
    try:
        await code_web_tools.native_search(code_web_tools.WebSearch(query="docs"), provider(), None, "m", {})
        transport(lambda request: httpx.Response(400, json={"error": {"message": "Unsupported web_search_20250305"}}))
        with pytest.raises(RuntimeError):
            await code_web_tools.native_search(code_web_tools.WebSearch(query="docs"), provider(), None, "m", {})
    finally:
        code_usage.ACTIVE.reset(token)
    from sqlalchemy import select
    async with db_sessionmaker() as session:
        rows = (await session.execute(select(CodeUsageCall).where(CodeUsageCall.chat_id == manager.runtime.chat_id))).scalars().all()
        assert sorted(row.status for row in rows) == ["estimated", "rejected"]
        assert next(row for row in rows if row.status == "estimated").prices["searches"] == 8
        assert next(row for row in rows if row.status == "rejected").charged_cost == 0


async def test_search_cancellation_records_unknown_charge_without_retry(transport, monkeypatch):
    calls = []
    class Account:
        async def reserve(self, *args, **kwargs):
            return "reservation"
        async def settle(self, reservation, usage, **kwargs):
            calls.append((reservation, usage, kwargs))
    def cancelled(request):
        raise asyncio.CancelledError
    requests = transport(cancelled)
    token = code_usage.ACTIVE.set(Account())
    try:
        with pytest.raises(asyncio.CancelledError):
            await code_web_tools.native_search(code_web_tools.WebSearch(query="docs"), provider(), None, "m", {})
    finally:
        code_usage.ACTIVE.reset(token)
    assert len(requests) == 1 and calls == [("reservation", None, {"failed": True, "rejected": False})]
