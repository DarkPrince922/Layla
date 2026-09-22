"""Автоповтор запросов к модели и подстройка под её возможности."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.services import project_agent, provider_errors, tool_chat
from app.services.provider_errors import CapabilityError, ProviderError, classify_rejection

pytestmark = pytest.mark.asyncio


async def _setup(client, model="m"):
    await client.post("/api/auth/register", json={"email": "net@example.com", "password": "hunter2hunter2"})
    await client.post("/api/providers", json={"name": "M", "kind": "openai_compatible",
                                              "base_url": "http://p/v1", "default_model": model, "active": True})
    return (await client.get("/api/providers")).json()[0]["id"]


def _script(monkeypatch, steps: list, calls: list):
    """Каждый шаг — либо исключение, либо (текст, частичный_текст_до_обрыва)."""
    async def stream_turn(provider, key, model, conversation, available, caps=None, **kw):
        calls.append({"caps": dict(caps or {}), "tools": [t["function"]["name"] for t in available]})
        step = steps[min(len(calls), len(steps)) - 1]
        if isinstance(step, tuple):
            partial, exc = step
            yield ("content", partial)
            raise exc
        if isinstance(step, BaseException):
            raise step
        yield ("content", step)

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream_turn)


async def _run(client):
    chat = (await client.post("/api/chats", json={"domain": "osint", "model": "m"})).json()
    job = (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "привет"})).json()
    for _ in range(300):
        await asyncio.sleep(0.02)
        state = (await client.get(f"/api/jobs/{job['id']}")).json()
        if state["status"] not in ("queued", "running"):
            break
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    return state, detail["messages"][-1]


async def test_retries_after_dropped_connection_without_duplicate_text(client, monkeypatch):
    await _setup(client)
    calls: list = []
    _script(monkeypatch, [("Обрыв…", httpx.ReadError("reset")), "Готово"], calls)
    state, last = await _run(client)
    assert state["status"] == "done", state
    assert len(calls) == 2
    assert last["content"] == "Готово"  # кусок оборванного хода не задвоился
    assert any("повтор 1 из 5" in s["text"] for s in state["steps"])


async def test_rate_limit_is_retried(client, monkeypatch):
    await _setup(client)
    calls: list = []
    _script(monkeypatch, [ProviderError("429", retryable=True, status=429), "ok"], calls)
    state, last = await _run(client)
    assert state["status"] == "done" and last["content"] == "ok" and len(calls) == 2


async def test_bad_request_is_not_retried(client, monkeypatch):
    await _setup(client)
    calls: list = []
    _script(monkeypatch, [ProviderError("Провайдер отклонил запрос (HTTP 400)", status=400)], calls)
    state, _ = await _run(client)
    assert state["status"] == "error" and len(calls) == 1


async def test_gives_up_after_five_retries(client, monkeypatch):
    await _setup(client)
    calls: list = []
    _script(monkeypatch, [httpx.ConnectError("down")], calls)
    state, _ = await _run(client)
    assert state["status"] == "error"
    assert len(calls) == 1 + len(provider_errors.RETRY_DELAYS)


async def test_model_without_tools_is_learned_and_remembered(client, monkeypatch):
    provider_id = await _setup(client)
    calls: list = []
    _script(monkeypatch, [CapabilityError("no tools", capability="tools", value=False), "Отвечаю без файлов"], calls)
    state, last = await _run(client)
    assert state["status"] == "done", state
    assert calls[1]["caps"] == {"tools": False}
    assert last["content"] == "Отвечаю без файлов"
    # Запомнено: в настройках модели инструменты выключены, следующий чат сразу без них.
    models = (await client.get(f"/api/providers/{provider_id}/models")).json()
    assert models == [] or all(m["tools"] is False for m in models if m["name"] == "m")
    calls.clear()
    _script(monkeypatch, ["сразу"], calls)
    await _run(client)
    assert calls[0]["caps"].get("tools") is False


async def test_tools_toggle_in_settings(client):
    provider_id = await _setup(client)
    saved = (await client.put(f"/api/providers/{provider_id}/models",
                              json={"models": [{"name": "m", "enabled": True, "tools": False}]})).json()
    assert saved == [{"name": "m", "enabled": True, "tools": False}]
    assert (await client.get(f"/api/providers/{provider_id}/models")).json()[0]["tools"] is False


def test_classify_rejections():
    o1 = ("Unsupported parameter: 'max_tokens' is not supported with this model. "
          "Use 'max_completion_tokens' instead.")
    assert classify_rejection(400, o1).capability == "token_param"
    assert classify_rejection(400, "registry.ollama.ai/library/gemma does not support tools").capability == "tools"
    assert classify_rejection(404, "No endpoints found that support tool use.").capability == "tools"
    assert classify_rejection(400, "messages.1.reasoning_content: Extra inputs are not permitted").capability \
        == "replay_reasoning"
    # Ошибка в схеме инструмента — не повод отключать инструменты.
    assert classify_rejection(400, "Invalid 'tools[0].function.parameters': bad schema") is None
    assert classify_rejection(401, "does not support tools") is None


async def test_stream_turn_applies_caps_to_payload(monkeypatch):
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        wire = ('data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, text=wire, headers={"content-type": "text/event-stream"})

    original = httpx.AsyncClient
    monkeypatch.setattr(tool_chat.httpx, "AsyncClient",
                        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))

    class P:  # минимальный провайдер
        kind, base_url = "openai_compatible", "http://p/v1"

    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "a", "reasoning_content": "r"}]
    tools = project_agent.TOOLS
    out = [x async for x in tool_chat.stream_turn(
        P, None, "o3", messages, tools,
        caps={"tools": False, "token_param": "max_completion_tokens", "replay_reasoning": False})]
    assert ("content", "ok") in out
    assert "tools" not in sent and "tool_choice" not in sent
    assert sent["max_completion_tokens"] == 8192 and "max_tokens" not in sent
    assert all("reasoning_content" not in m for m in sent["messages"])


async def test_rejection_body_becomes_capability_error(monkeypatch):
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "model does not support tools"}})

    original = httpx.AsyncClient
    monkeypatch.setattr(tool_chat.httpx, "AsyncClient",
                        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))

    class P:
        kind, base_url = "openai_compatible", "http://p/v1"

    with pytest.raises(CapabilityError) as info:
        async for _ in tool_chat.stream_turn(P, None, "m", [{"role": "user", "content": "x"}], project_agent.TOOLS):
            pass
    assert info.value.capability == "tools"


async def test_design_generation_retries(client, monkeypatch):
    await _setup(client)
    import app.services.provider_client as pc

    attempts = {"n": 0}

    async def flaky(provider, key, model, messages, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("down")
        yield ("content", "```html\n<h1>ok</h1>\n```")

    monkeypatch.setattr(pc, "stream_chat", flaky)
    job = (await client.post("/api/designs/generate", json={"stack": "html", "brief": {}})).json()
    for _ in range(300):
        await asyncio.sleep(0.02)
        state = (await client.get(f"/api/jobs/{job['id']}")).json()
        if state["status"] not in ("queued", "running"):
            break
    assert state["status"] == "done", state
    assert attempts["n"] == 2
    assert "<h1>ok</h1>" in (await client.get("/api/designs")).json()[0]["files"][0]["content"]
