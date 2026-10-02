"""Отказы провайдера: понятная причина, запасные ключи, временные ошибки шлюзов."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.models.provider import Provider
from app.services import provider_client, tool_chat
from app.services.provider_errors import http_error, provider_message

_REAL_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _fresh_resting():
    provider_client._RESTING.clear()
    yield
    provider_client._RESTING.clear()


def _ok(text="Готово"):
    events = [{"choices": [{"delta": {"content": text}}]},
              {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
    wire = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(200, text=wire, headers={"content-type": "text/event-stream"})


def _http(monkeypatch, answer):
    """answer(request_index, authorization, body) -> httpx.Response. Возвращает журнал запросов."""
    log: list[dict] = []

    def handler(request):
        body = json.loads(request.content)
        log.append({"auth": request.headers.get("authorization"), "body": body})
        return answer(len(log) - 1, request.headers.get("authorization"), body)

    monkeypatch.setattr(tool_chat.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    return log


async def _setup(client, *keys):
    await client.post("/api/auth/register", json={"email": "keys@example.com", "password": "hunter2hunter2"})
    await client.post("/api/providers", json={"name": "M", "kind": "openai_compatible",
                                              "base_url": "http://p/v1", "default_model": "m", "active": True})
    provider_id = (await client.get("/api/providers")).json()[0]["id"]
    ids = []
    for label, secret in keys:
        r = await client.post(f"/api/providers/{provider_id}/keys", json={"api_key": secret, "label": label})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
        await asyncio.sleep(0.01)  # разное время добавления — порядок ключей стабильный
    return provider_id, ids


async def _run(client, text="привет"):
    chat = (await client.post("/api/chats", json={"domain": "osint", "model": "m"})).json()
    job = (await client.post(f"/api/chats/{chat['id']}/run", json={"content": text})).json()
    for _ in range(400):
        await asyncio.sleep(0.02)
        state = (await client.get(f"/api/jobs/{job['id']}")).json()
        if state["status"] not in ("queued", "running"):
            break
    return state


def _deny(status, message, **extra):
    return httpx.Response(status, json={"error": {"message": message, **extra}})


# --- Причина отказа -----------------------------------------------------------

def test_reason_is_taken_from_provider_json():
    assert provider_message('{"error": {"message": "Invalid model"}}') == "Invalid model"
    openrouter = json.dumps({"error": {"message": "Provider returned error", "code": 400, "metadata": {
        "raw": json.dumps({"error": {"message": "messages: text content blocks must be non-empty"}}),
        "provider_name": "Anthropic"}}})
    assert provider_message(openrouter) == ("Provider returned error: messages: text content blocks "
                                            "must be non-empty [Anthropic]")
    assert provider_message('{"detail": "Not found"}') == "Not found"


def test_reason_never_shows_raw_text_or_keys():
    # Произвольный текст и HTML — только в лог (raw=True), пользователю не показываем.
    assert provider_message("secret-key-do-not-leak") == ""
    assert provider_message("<html><h1>502 Bad Gateway</h1></html>", raw=True) == "502 Bad Gateway"
    leaked = '{"error": {"message": "Incorrect API key provided: sk-proj-abcdefgh12345678"}}'
    assert "abcdefgh" not in provider_message(leaked)
    echoed = '{"error": {"message": "key my-own-secret-token is invalid"}}'
    assert "my-own-secret-token" not in provider_message(echoed, secret="my-own-secret-token")


def test_status_decides_retry_and_key_switch():
    unauthorized = http_error(401, '{"error": {"message": "Invalid API key"}}')
    assert (unauthorized.retryable, unauthorized.key_failed) == (False, True)
    assert "не принял ключ (HTTP 401): Invalid API key" in str(unauthorized)

    limited = http_error(429, '{"error": {"message": "Rate limit reached"}}', httpx.Headers({"retry-after": "20"}))
    assert (limited.retryable, limited.key_failed, limited.retry_after) == (True, True, 20.0)
    assert "ограничил частоту" in str(limited)

    quota = http_error(429, "{}", httpx.Headers({"retry-after": "7200"}))
    assert not quota.retryable and "примерно через 120 мин" in str(quota)

    flaky = http_error(400, '{"error": {"message": "Provider returned error"}}', tools=True)
    assert flaky.retryable and not flaky.key_failed
    assert "отклонил запрос с инструментами (HTTP 400): Provider returned error" in str(flaky)

    from app.services.provider_errors import classify_rejection

    wants_string = classify_rejection(400, '{"error": {"message": "messages.2.content: Input should be a valid string"}}')
    assert (wants_string.capability, wants_string.value) == ("tool_content", "")
    # Обратная жалоба шлюза к Claude — на пустую строку, а мы и так шлём null.
    assert classify_rejection(400, '{"error": {"message": "text content blocks must be non-empty"}}') is None

    bad = http_error(400, '{"error": {"message": "Unknown field foo"}}')
    assert not bad.retryable and "Unknown field foo" in str(bad)
    assert http_error(503, "").retryable and not http_error(501, "").retryable


# --- Запросы ------------------------------------------------------------------

async def test_tool_only_turn_sends_null_content(monkeypatch):
    log = _http(monkeypatch, lambda i, auth, body: _ok())

    class P:
        kind, base_url, name = "openai_compatible", "http://p/v1", "P"

    history = [
        {"role": "user", "content": "создай файл"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "[]", "is_error": False},
    ]
    async for _ in tool_chat.stream_turn(P, "k", "m", history, []):
        pass
    sent = log[0]["body"]["messages"]
    assert sent[1]["content"] is None and sent[1]["tool_calls"]
    assert "is_error" not in sent[2]


async def test_server_that_wants_string_content_is_learned(client, monkeypatch, db_sessionmaker):
    """Серверу, который не принимает null в content, после первого отказа шлём пустую строку."""
    provider_id, _ = await _setup(client)
    write = {"path": "a.txt", "content": "x", "expected_sha256": None}
    call = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "type": "function",
            "function": {"name": "write_file", "arguments": json.dumps(write)}}]}}]}
    stop = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
    wire = "".join("data: " + json.dumps(e) + "\n\n" for e in (call, stop)) + "data: [DONE]\n\n"

    def answer(i, auth, body):
        if i == 0:
            return httpx.Response(200, text=wire, headers={"content-type": "text/event-stream"})
        if any(m.get("tool_calls") and m.get("content") is None for m in body["messages"]):
            return _deny(400, "messages.2.content: Input should be a valid string")
        return _ok("Записал")

    log = _http(monkeypatch, answer)
    chat = (await client.post("/api/chats", json={"domain": "code", "model": "m"})).json()
    job = (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "запиши"})).json()
    for _ in range(300):
        await asyncio.sleep(0.02)
        state = (await client.get(f"/api/jobs/{job['id']}")).json()
        if state["status"] not in ("queued", "running"):
            break
    assert state["status"] == "done", state
    assert [m for m in log[-1]["body"]["messages"] if m.get("tool_calls")][0]["content"] == ""
    async with db_sessionmaker() as s:
        assert (await s.get(Provider, provider_id)).model_caps["m"]["tool_content"] == ""


async def test_rejected_key_switches_to_next(client, monkeypatch):
    await _setup(client, ("Основной", "key-one-111111"), ("Запасной", "key-two-222222"))

    def answer(i, auth, body):
        return _deny(401, "Invalid API key") if auth == "Bearer key-one-111111" else _ok()

    log = _http(monkeypatch, answer)
    state = await _run(client)
    assert state["status"] == "done", state
    assert [r["auth"] for r in log] == ["Bearer key-one-111111", "Bearer key-two-222222"]
    assert any("отклонил ключ (HTTP 401) — пробую Запасной" in s["text"] for s in state["steps"])

    # Отказавший ключ какое-то время идёт последним: следующая задача сразу берёт рабочий.
    log.clear()
    assert (await _run(client))["status"] == "done"
    assert [r["auth"] for r in log] == ["Bearer key-two-222222"]


async def test_disabled_key_is_skipped_and_order_is_stable(client, monkeypatch):
    provider_id, ids = await _setup(client, ("A", "key-aaa-111111"), ("B", "key-bbb-222222"), ("C", "key-ccc-333333"))
    log = _http(monkeypatch, lambda i, auth, body: _ok())
    for _ in range(3):
        await _run(client)
    assert {r["auth"] for r in log} == {"Bearer key-aaa-111111"}  # всегда первый добавленный

    await client.post(f"/api/providers/keys/{ids[0]}/status", json={"status": "disabled"})
    log.clear()
    await _run(client)
    assert [r["auth"] for r in log] == ["Bearer key-bbb-222222"]


async def test_every_key_rejected_gives_clear_reason(client, monkeypatch):
    await _setup(client, ("A", "key-aaa-111111"), ("B", "key-bbb-222222"))
    log = _http(monkeypatch, lambda i, auth, body: _deny(402, "Insufficient credits"))
    state = await _run(client)
    assert state["status"] == "error"
    assert len(log) == 2  # каждый ключ попробован один раз
    assert "закончились средства или квота (HTTP 402): Insufficient credits" in state["error"]


async def test_gateway_hiccup_is_retried(client, monkeypatch):
    await _setup(client)
    log = _http(monkeypatch, lambda i, auth, body: _deny(400, "Provider returned error") if i == 0 else _ok("ок"))
    state = await _run(client)
    assert state["status"] == "done", state
    assert len(log) == 2


async def test_rate_limit_on_single_key_reports_reason(client, monkeypatch):
    await _setup(client, ("A", "key-aaa-111111"))
    log = _http(monkeypatch, lambda i, auth, body: _deny(429, "Rate limit exceeded: free-models-per-min"))
    state = await _run(client)
    assert state["status"] == "error"
    assert len(log) == 6  # первая попытка и 5 повторов
    assert "ограничил частоту запросов (HTTP 429): Rate limit exceeded: free-models-per-min" in state["error"]


async def test_design_generation_switches_keys_too(client, monkeypatch):
    await _setup(client, ("A", "key-aaa-111111"), ("B", "key-bbb-222222"))

    def answer(i, auth, body):
        if auth == "Bearer key-aaa-111111":
            return _deny(403, "Key suspended")
        return _ok("```html\n<h1>ok</h1>\n```")

    _http(monkeypatch, answer)
    job = (await client.post("/api/designs/generate", json={"stack": "html", "brief": {}})).json()
    for _ in range(300):
        await asyncio.sleep(0.02)
        state = (await client.get(f"/api/jobs/{job['id']}")).json()
        if state["status"] not in ("queued", "running"):
            break
    assert state["status"] == "done", state
    assert any("HTTP 403" in s["text"] for s in state["steps"])
