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
    assert [(m["name"], m["tools"]) for m in models] == [("m", False)]
    calls.clear()
    _script(monkeypatch, ["сразу"], calls)
    await _run(client)
    assert calls[0]["caps"].get("tools") is False


async def test_tools_toggle_in_settings(client):
    provider_id = await _setup(client)
    saved = (await client.put(f"/api/providers/{provider_id}/models",
                              json={"models": [{"name": "m", "enabled": True, "tools": False}]})).json()
    assert saved[0]["name"] == "m" and saved[0]["enabled"] is True and saved[0]["tools"] is False
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


# --- Лимит длины и причины завершения -----------------------------------------

def _wire(*, text="", call=None, finish="stop", done=True):
    events = []
    if text:
        events.append({"choices": [{"delta": {"content": text}}]})
    if call:
        events.append({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                       "function": {"name": call[0], "arguments": json.dumps(call[1])}}]}}]})
    events.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    wire = "".join("data: " + json.dumps(e) + "\n\n" for e in events)
    return httpx.Response(200, text=wire + ("data: [DONE]\n\n" if done else ""),
                          headers={"content-type": "text/event-stream"})


_REAL_CLIENT = httpx.AsyncClient  # до любых подмен: повторный _http не должен оборачивать подмену


def _http(monkeypatch, responses: list, sent: list):
    original = _REAL_CLIENT

    def handler(request):
        sent.append(json.loads(request.content))
        return responses[min(len(sent), len(responses)) - 1]

    monkeypatch.setattr(tool_chat.httpx, "AsyncClient",
                        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


async def test_truncated_file_write_raises_output_limit_and_retries(client, monkeypatch):
    """Большой файл не влез в 8192 токенов — лимит растёт, ход повторяется, лимит запоминается."""
    await _setup(client)
    write = ("write_file", {"path": "index.html", "content": "<h1>ok</h1>", "expected_sha256": None})
    sent: list = []
    _http(monkeypatch, [_wire(call=write, finish="length"), _wire(call=write, finish="tool_calls"),
                        _wire(text="Готово")], sent)
    state, last = await _run(client)
    assert state["status"] == "done", state
    assert [r["max_tokens"] for r in sent] == [8192, 16384, 16384]
    assert last["meta"]["tools"][0]["status"] == "done"
    assert any("лимит длины увеличен до 16384" in s["text"] for s in state["steps"])
    # Следующий чат с этой моделью сразу просит 16384.
    sent.clear()
    _http(monkeypatch, [_wire(text="ok")], sent)
    await _run(client)
    assert sent[0]["max_tokens"] == 16384


@pytest.mark.parametrize("finish,done", [
    ("stop", True), ("end_turn", True), ("eos", True), ("STOP", True), (None, True), ("stop", False),
])
async def test_normal_endings_are_not_errors(client, monkeypatch, finish, done):
    """[DONE] без причины, нестандартные названия и текст без [DONE] — это нормальный конец."""
    await _setup(client)
    _http(monkeypatch, [_wire(text="Привет", finish=finish, done=done)], [])
    state, last = await _run(client)
    assert state["status"] == "done", state
    assert last["content"] == "Привет"


async def test_tool_call_without_stream_end_is_never_executed(client, monkeypatch):
    await _setup(client)
    write = ("write_file", {"path": "x.html", "content": "x", "expected_sha256": None})
    _http(monkeypatch, [_wire(call=write, finish="tool_calls", done=False)], [])
    state, last = await _run(client)
    assert state["status"] == "error"
    assert not [t for t in (last["meta"].get("tools") or []) if t.get("status") == "done"]


async def test_provider_output_cap_is_learned(client, monkeypatch):
    """Провайдер не даёт больше 8192 — лимит снижается до его предела и запоминается."""
    provider_id = await _setup(client)
    from sqlalchemy import update

    from app.db import get_sessionmaker
    from app.main import app
    from app.models.provider import Provider

    maker = app.dependency_overrides[get_sessionmaker]()
    async with maker() as s:
        await s.execute(update(Provider).where(Provider.id == provider_id)
                        .values(model_caps={"m": {"max_output": 32768}}))
        await s.commit()
    sent: list = []
    too_big = httpx.Response(400, json={"error": {"message": "max_tokens: Input should be less than or equal to 8192"}})
    _http(monkeypatch, [too_big, _wire(text="ok")], sent)
    state, last = await _run(client)
    assert state["status"] == "done", state
    assert [r["max_tokens"] for r in sent] == [32768, 8192]


async def test_long_text_at_ceiling_is_kept_with_hint(client, monkeypatch):
    await _setup(client)
    monkeypatch.setattr(project_agent, "MAX_OUTPUT_CEILING", 8192)
    _http(monkeypatch, [_wire(text="Длинный ответ…", finish="length")], [])
    state, last = await _run(client)
    assert state["status"] == "done", state
    assert last["content"].startswith("Длинный ответ…") and "продолжай" in last["content"]


def test_classify_output_cap():
    err = classify_rejection(400, "max_tokens: Input should be less than or equal to 8192")
    assert err.capability == "max_output" and err.value == 8192
    err = classify_rejection(400, "max_tokens is too large: 65536. This model supports at most 16384 completion tokens")
    assert err.capability == "max_output" and err.value == 16384
    o1 = "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead."
    assert classify_rejection(400, o1).capability == "token_param"


# --- Настройки модели: длина ответа, контекст, температура, глубина размышлений --

async def _settings(client, provider_id, **fields):
    body = {"name": "m", "enabled": True, "tools": True, **fields}
    r = await client.put(f"/api/providers/{provider_id}/models", json={"models": [body]})
    assert r.status_code == 200, r.text
    return r.json()[0]


async def test_model_settings_round_trip_and_back_to_auto(client):
    provider_id = await _setup(client)
    saved = await _settings(client, provider_id, max_output=4096, max_output_manual=True, context=32000,
                            temperature=0.3, reasoning_effort="high")
    assert (saved["max_output"], saved["max_output_manual"], saved["context"],
            saved["temperature"], saved["reasoning_effort"]) == (4096, True, 32000, 0.3, "high")
    auto = await _settings(client, provider_id)
    assert (auto["max_output"], auto["max_output_manual"], auto["context"], auto["temperature"],
            auto["reasoning_effort"]) == (None, False, None, None, None)
    bad = await client.put(f"/api/providers/{provider_id}/models",
                           json={"models": [{"name": "m", "temperature": 5}]})
    assert bad.status_code == 422


async def test_settings_reach_the_request(client, monkeypatch):
    provider_id = await _setup(client)
    await _settings(client, provider_id, max_output=4096, max_output_manual=True, temperature=0.3,
                    reasoning_effort="low")
    sent: list = []
    _http(monkeypatch, [_wire(text="ok")], sent)
    await _run(client)
    assert (sent[0]["max_tokens"], sent[0]["temperature"], sent[0]["reasoning_effort"]) == (4096, 0.3, "low")


async def test_manual_output_limit_is_a_ceiling(client, monkeypatch):
    provider_id = await _setup(client)
    await _settings(client, provider_id, max_output=4096, max_output_manual=True)
    write = ("write_file", {"path": "a.html", "content": "x", "expected_sha256": None})
    sent: list = []
    _http(monkeypatch, [_wire(call=write, finish="length")], sent)
    state, _ = await _run(client)
    assert state["status"] == "error" and len(sent) == 1  # ручной потолок не поднимаем сами
    assert "Макс. токенов ответа" in state["error"]


async def test_unsupported_param_is_dropped_and_remembered(client, monkeypatch):
    provider_id = await _setup(client)
    await _settings(client, provider_id, temperature=0.7)
    rejected = httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature' does not support 0.7 "
                                                               "with this model. Only the default (1) value is supported."}})
    sent: list = []
    _http(monkeypatch, [rejected, _wire(text="ok")], sent)
    state, last = await _run(client)
    assert state["status"] == "done" and last["content"] == "ok"
    assert "temperature" in sent[0] and "temperature" not in sent[1]
    model = (await client.get(f"/api/providers/{provider_id}/models")).json()[0]
    assert model["dropped"] == ["temperature"] and model["temperature"] == 0.7
    # «Сбросить подобранное» возвращает параметр в запрос.
    await _settings(client, provider_id, temperature=0.7, reset=True)
    assert (await client.get(f"/api/providers/{provider_id}/models")).json()[0]["dropped"] == []


async def _chat_with_history(client, db_sessionmaker, turns: int, size: int = 600):
    from datetime import UTC, datetime, timedelta

    from app.models.chat import Message

    chat = (await client.post("/api/chats", json={"domain": "osint", "model": "m"})).json()
    start = datetime.now(UTC) - timedelta(hours=1)
    async with db_sessionmaker() as s:
        for i in range(turns):
            role = "user" if i % 2 == 0 else "assistant"
            s.add(Message(chat_id=chat["id"], role=role, content=f"#{i} " + "история " * (size // 8),
                          created_at=start + timedelta(seconds=i)))
        await s.commit()
    return chat


async def _run_in(client, chat):
    job = (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "новый вопрос"})).json()
    for _ in range(300):
        await asyncio.sleep(0.02)
        state = (await client.get(f"/api/jobs/{job['id']}")).json()
        if state["status"] not in ("queued", "running"):
            return state
    return state


async def test_history_is_trimmed_to_context(client, monkeypatch, db_sessionmaker):
    provider_id = await _setup(client)
    await _settings(client, provider_id, context=4096, max_output=1024, max_output_manual=True)
    chat = await _chat_with_history(client, db_sessionmaker, turns=20)
    sent: list = []
    _http(monkeypatch, [_wire(text="ok")], sent)
    state = await _run_in(client, chat)
    assert state["status"] == "done", state
    roles = [m["role"] for m in sent[0]["messages"]]
    assert roles[0] == "system" and roles[1] == "user"  # история начинается с пользователя
    assert sent[0]["messages"][-1]["content"] == "новый вопрос"
    assert 3 < len(roles) < 22  # старое отрезано, свежее осталось
    assert "#19" in sent[0]["messages"][-2]["content"]
    assert any("старых" in s["text"] for s in state["steps"])
    # В самом чате история цела.
    assert len((await client.get(f"/api/chats/{chat['id']}")).json()["messages"]) == 22


async def test_context_overflow_is_learned(client, monkeypatch, db_sessionmaker):
    provider_id = await _setup(client)
    chat = await _chat_with_history(client, db_sessionmaker, turns=30)
    overflow = httpx.Response(400, json={"error": {"message": "This model's maximum context length is 4096 tokens. "
                                                               "However, you requested 9000 tokens."}})
    sent: list = []
    _http(monkeypatch, [overflow, _wire(text="ok")], sent)
    state = await _run_in(client, chat)
    assert state["status"] == "done", state
    assert len(sent[1]["messages"]) < len(sent[0]["messages"])
    model = (await client.get(f"/api/providers/{provider_id}/models")).json()
    assert model[0]["name"] == "m" and model[0]["context"] == 4096


def test_classify_context_and_params():
    err = classify_rejection(400, "This model's maximum context length is 32768 tokens. However, you requested "
                                  "40000 tokens (31808 in the messages, 8192 in the completion).")
    assert (err.capability, err.value) == ("context", 32768)
    assert classify_rejection(400, "prompt is too long: 210000 tokens > 200000 maximum").value == 200000
    assert classify_rejection(400, "Unrecognized request argument supplied: reasoning_effort").value == "reasoning_effort"
