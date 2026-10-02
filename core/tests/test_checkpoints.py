"""Контрольные точки: откат файлов к состоянию до ответа и продолжение прерванной работы."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.models.chat import Message
from app.models.user import Project
from app.services import design_gen, history, tool_chat
from app.services.provider_errors import ProviderError

pytestmark = pytest.mark.asyncio


async def _setup(client):
    await client.post("/api/auth/register", json={"email": "cp@example.com", "password": "hunter2hunter2"})
    await client.post("/api/providers", json={"name": "M", "kind": "openai_compatible",
                                              "base_url": "http://p/v1", "default_model": "m", "active": True})


def _call(name, args, call_id):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _script(monkeypatch, turns: list):
    """Каждый элемент — ответ модели на очередной запрос: список вызовов или текст."""
    queue = list(turns)

    async def stream_turn(provider, key, model, messages, tools, caps=None):
        step = queue.pop(0)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, tuple):
            yield ("content", step[0])
            raise step[1]
        if isinstance(step, list):
            yield ("tool_calls", step)
        else:
            yield ("content", step)

    monkeypatch.setattr(tool_chat, "stream_turn", stream_turn)


async def _say(client, chat_id, text):
    job = (await client.post(f"/api/chats/{chat_id}/run", json={"content": text})).json()
    for _ in range(300):
        await asyncio.sleep(0.02)
        state = (await client.get(f"/api/jobs/{job['id']}")).json()
        if state["status"] not in ("queued", "running"):
            return state
    return state


async def _root(db_sessionmaker, project_id) -> Path:
    async with db_sessionmaker() as s:
        return Path((await s.get(Project, project_id)).path)


async def test_rollback_restores_files_to_before_the_answer(client, monkeypatch, db_sessionmaker):
    await _setup(client)
    chat = (await client.post("/api/chats", json={"domain": "design", "model": "m"})).json()
    _script(monkeypatch, [
        [_call("write_file", {"path": "index.html", "content": "<h1>v1</h1>", "expected_sha256": None}, "a")],
        "Создал страницу",
        [_call("edit_file", {"path": "index.html", "old_string": "v1", "new_string": "v2"}, "b"),
         _call("write_file", {"path": "style.css", "content": "h1{}", "expected_sha256": None}, "c")],
        "Поправил",
    ])
    assert (await _say(client, chat["id"], "сделай страницу"))["status"] == "done"
    assert (await _say(client, chat["id"], "поменяй заголовок"))["status"] == "done"
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    root = await _root(db_sessionmaker, detail["project_id"])
    assert (root / "index.html").read_text() == "<h1>v2</h1>" and (root / "style.css").exists()
    first, second = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert first["meta"]["checkpoint"] and second["meta"]["checkpoint"]

    r = await client.post(f"/api/chats/{chat['id']}/messages/{second['id']}/rollback")
    assert r.status_code == 200, r.text
    assert r.json() == {"restored": ["index.html", "style.css"], "messages": 1}
    assert (root / "index.html").read_text() == "<h1>v1</h1>" and not (root / "style.css").exists()
    messages = (await client.get(f"/api/chats/{chat['id']}")).json()["messages"]
    assert [m["meta"].get("rolled_back") for m in messages if m["role"] == "assistant"] == [None, True]
    # Модель узнаёт об откате из истории.
    async with db_sessionmaker() as s:
        rows = await history.messages(s, chat["id"])
        assert "rolled back by the user" in history.entry(next(m for m in rows if m.id == second["id"]))["content"]

    # Откат к первому ответу: страницы ещё не было.
    assert (await client.post(f"/api/chats/{chat['id']}/messages/{first['id']}/rollback")).status_code == 200
    assert not (root / "index.html").exists()
    # Точки использованы — повторно откатывать нечего.
    again = await client.post(f"/api/chats/{chat['id']}/messages/{first['id']}/rollback")
    assert again.status_code == 400


async def test_rollback_is_private_and_waits_for_running_job(client, monkeypatch, db_sessionmaker):
    await _setup(client)
    chat = (await client.post("/api/chats", json={"domain": "code", "model": "m"})).json()
    _script(monkeypatch, [
        [_call("write_file", {"path": "a.txt", "content": "x", "expected_sha256": None}, "a")], "ok"])
    await _say(client, chat["id"], "запиши")
    answer = [m for m in (await client.get(f"/api/chats/{chat['id']}")).json()["messages"] if m["role"] == "assistant"][0]
    await client.post("/api/auth/logout")
    await client.post("/api/auth/register", json={"email": "other@example.com", "password": "hunter2hunter2"})
    assert (await client.post(f"/api/chats/{chat['id']}/messages/{answer['id']}/rollback")).status_code == 404


async def test_interrupted_turn_is_described_for_continuation(client, monkeypatch, db_sessionmaker):
    await _setup(client)
    chat = (await client.post("/api/chats", json={"domain": "design", "model": "m"})).json()
    _script(monkeypatch, [
        [_call("write_file", {"path": "index.html", "content": "<h1>1</h1>", "expected_sha256": None}, "a")],
        ProviderError("Провайдер не принял ключ (HTTP 401)"),
    ])
    state = await _say(client, chat["id"], "сделай лендинг")
    assert state["status"] == "error"
    async with db_sessionmaker() as s:
        answer = [m for m in await history.messages(s, chat["id"]) if m.role == "assistant"][0]
        described = history.entry(answer)["content"]
    assert "This turn was interrupted: Провайдер не принял ключ (HTTP 401)" in described
    assert "write_file index.html — done" in described and "without repeating completed steps" in described

    # «Продолжить» — обычное сообщение: модель видит, что уже сделано.
    seen: list = []

    async def stream_turn(provider, key, model, messages, tools, caps=None):
        seen.append(messages)
        yield ("content", "Продолжил")

    monkeypatch.setattr(tool_chat, "stream_turn", stream_turn)
    assert (await _say(client, chat["id"], "Продолжи с того места, где остановился"))["status"] == "done"
    assert any("This turn was interrupted" in (m.get("content") or "") for m in seen[0])


# --- Генерация по брифу: продолжить с черновика ---------------------------------

async def _wait(client, job_id):
    for _ in range(300):
        await asyncio.sleep(0.02)
        state = (await client.get(f"/api/jobs/{job_id}")).json()
        if state["status"] not in ("queued", "running"):
            return state
    return state


async def test_failed_design_generation_resumes_from_draft(client, monkeypatch):
    await _setup(client)
    calls: list = []
    steps = [("```html\n<main><h1>Начало длинного лендинга студии", ProviderError("down")),
             "</h1><p>конец</p></main>\n```"]

    async def stream_turn(provider, key, model, messages, tools, caps=None):
        calls.append(messages)
        step = steps.pop(0)
        if isinstance(step, tuple):
            yield ("content", step[0])
            raise step[1]
        yield ("content", step)

    monkeypatch.setattr(tool_chat, "stream_turn", stream_turn)
    brief = {"artifact_type": "Лендинг", "brand": "Nord"}
    failed = await _wait(client, (await client.post("/api/designs/generate",
                                                    json={"stack": "html", "brief": brief})).json()["id"])
    assert failed["status"] == "error"
    assert failed["result"]["draft"] == "```html\n<main><h1>Начало длинного лендинга студии"
    assert failed["result"]["brief"]["brand"] == "Nord"

    resumed = await client.post(f"/api/designs/generate/{failed['id']}/resume")
    assert resumed.status_code == 202, resumed.text
    state = await _wait(client, resumed.json()["id"])
    assert state["status"] == "done", state
    assert calls[1][-2] == {"role": "assistant", "content": failed["result"]["draft"]}
    assert calls[1][-1]["content"] == design_gen.CONTINUE_PROMPT
    design = (await client.get(f"/api/designs/{state['result']['design_id']}")).json()
    assert design["files"][0]["content"] == "<main><h1>Начало длинного лендинга студии</h1><p>конец</p></main>"
    assert design["brief"]["brand"] == "Nord"
    assert any("Продолжаю с черновика" in s["text"] for s in state["steps"])


async def test_resume_needs_a_draft(client, monkeypatch, db_sessionmaker):
    await _setup(client)

    async def stream_turn(provider, key, model, messages, tools, caps=None):
        raise ProviderError("down")
        yield  # pragma: no cover

    monkeypatch.setattr(tool_chat, "stream_turn", stream_turn)
    failed = await _wait(client, (await client.post("/api/designs/generate",
                                                    json={"stack": "html", "brief": {}})).json()["id"])
    assert (await client.post(f"/api/designs/generate/{failed['id']}/resume")).status_code == 400
    assert (await client.post("/api/designs/generate/nope/resume")).status_code == 404
    async with db_sessionmaker() as s:
        assert await s.get(Message, "nope") is None
