"""Короткие размышления и работа по шагам: бюджет, ограничитель, план, правила проекта, ревью."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.models.chat import Message
from app.models.user import Project
from app.services import design_gen, history, project_agent, tool_chat
from app.services.provider_errors import classify_rejection
from tests.test_project_agent import provider_response

pytestmark = pytest.mark.asyncio
_REAL_CLIENT = httpx.AsyncClient


async def _setup(client, *, base_url="http://p/v1", kind="openai_compatible", model="m"):
    await client.post("/api/auth/register", json={"email": "think@example.com", "password": "hunter2hunter2"})
    await client.post("/api/providers", json={"name": "M", "kind": kind, "base_url": base_url,
                                              "default_model": model, "active": True})
    return (await client.get("/api/providers")).json()[0]["id"]


async def _settings(client, provider_id, model="m", **fields):
    body = {"name": model, "enabled": True, "tools": True, **fields}
    r = await client.put(f"/api/providers/{provider_id}/models", json={"models": [body]})
    assert r.status_code == 200, r.text
    return r.json()[0]


def _script(monkeypatch, steps: list, calls: list):
    """Каждый шаг — функция (conversation) -> список событий stream_turn."""
    async def stream_turn(provider, key, model, conversation, available, caps=None, **kw):
        calls.append({"caps": dict(caps or {}), "conversation": list(conversation),
                      "tools": {t["function"]["name"] for t in available}})
        for event in steps[min(len(calls), len(steps)) - 1]:
            yield event

    monkeypatch.setattr(tool_chat, "stream_turn", stream_turn)


async def _run(client, domain="osint", text="сделай", mode="auto", model="m"):
    chat = (await client.post("/api/chats", json={"domain": domain, "model": model})).json()
    job = (await client.post(f"/api/chats/{chat['id']}/run", json={"content": text, "mode": mode})).json()
    for _ in range(300):
        await asyncio.sleep(0.02)
        state = (await client.get(f"/api/jobs/{job['id']}")).json()
        if state["status"] not in ("queued", "running"):
            break
    return chat, state


# --- Ограничитель размышлений ----------------------------------------------------

async def test_runaway_reasoning_is_stopped_and_retried_briefly(client, monkeypatch):
    provider_id = await _setup(client)
    await _settings(client, provider_id, reasoning_budget=1000)  # ~3000 символов
    calls: list = []
    runaway = [("reasoning", "<html>" + "x" * 500) for _ in range(10)] + [("content", "не дойдёт")]
    _script(monkeypatch, [runaway, [("reasoning", "План: один шаг."), ("content", "Готово")]], calls)
    _, state = await _run(client)
    assert state["status"] == "done", state
    assert len(calls) == 2
    assert calls[1]["conversation"][-1]["content"].startswith("[Layla] Your reasoning ran past its budget (~1000")
    assert calls[1]["caps"]["reasoning_effort"] == "low"
    assert any("размышляла дольше бюджета" in s["text"] for s in state["steps"])
    assert "размышления остановлены" in state["reasoning"]


async def test_guard_trips_once_per_turn_and_can_be_disabled(client, monkeypatch):
    provider_id = await _setup(client)
    await _settings(client, provider_id, reasoning_budget=1000)
    calls: list = []
    runaway = [("reasoning", "y" * 4000), ("content", "Ответ после долгих раздумий")]
    _script(monkeypatch, [runaway], calls)
    chat, state = await _run(client)
    # Второй раз за ход не прерываем — модель доводит ответ.
    assert state["status"] == "done" and len(calls) == 2
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    assert detail["messages"][-1]["content"] == "Ответ после долгих раздумий"

    await _settings(client, provider_id, reasoning_budget=0)  # без ограничения
    calls.clear()
    _, state = await _run(client)
    assert state["status"] == "done" and len(calls) == 1


async def test_answer_already_started_is_not_cut(client, monkeypatch):
    provider_id = await _setup(client)
    await _settings(client, provider_id, reasoning_budget=1000)
    calls: list = []
    _script(monkeypatch, [[("content", "Начал отвечать. "), ("reasoning", "z" * 5000), ("content", "Дописал")]], calls)
    chat, state = await _run(client)
    assert state["status"] == "done" and len(calls) == 1


async def test_old_reasoning_is_not_sent_again(client, monkeypatch):
    await _setup(client)
    calls: list = []
    _script(monkeypatch, [[("reasoning", "черновик: <html>…весь код…</html>"), ("content", "Готово")]], calls)
    chat, _ = await _run(client)
    job = (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "дальше"})).json()
    for _ in range(300):
        await asyncio.sleep(0.02)
        if (await client.get(f"/api/jobs/{job['id']}")).json()["status"] not in ("queued", "running"):
            break
    history_sent = calls[-1]["conversation"]
    assert all("reasoning_content" not in m for m in history_sent)
    assert "черновик" not in json.dumps(history_sent, ensure_ascii=False)


# --- Параметры провайдеров --------------------------------------------------------

def _capture(monkeypatch, response):
    sent: list = []

    def handler(request):
        sent.append(json.loads(request.content))
        return response

    monkeypatch.setattr(tool_chat.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    return sent


def _provider(kind="openai_compatible", base_url="http://p/v1"):
    class P:
        pass
    P.kind, P.base_url, P.name = kind, base_url, "P"
    return P


async def _one(provider, model, messages, caps):
    return [x async for x in tool_chat.stream_turn(provider, "k", model, messages, [], caps=caps)]


async def test_budget_and_effort_reach_each_provider_in_its_format(monkeypatch):
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    sent = _capture(monkeypatch, provider_response(False, text="ok"))
    await _one(_provider(base_url="https://openrouter.ai/api/v1"), "deepseek/r1", messages,
               {"reasoning_budget": 2048, "reasoning_effort": "high"})
    assert sent[-1]["reasoning"] == {"max_tokens": 2048} and "reasoning_effort" not in sent[-1]
    await _one(_provider(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"), "qwen3", messages,
               {"reasoning_budget": 2048, "reasoning_effort": "low"})
    assert sent[-1]["thinking_budget"] == 2048 and sent[-1]["reasoning_effort"] == "low"
    await _one(_provider(), "gpt-x", messages, {"reasoning_effort": "low", "reasoning_budget": 2048})
    assert sent[-1]["reasoning_effort"] == "low" and "reasoning" not in sent[-1] and "thinking_budget" not in sent[-1]

    sent = _capture(monkeypatch, provider_response(True, text="ok"))
    await _one(_provider("anthropic", "https://api.anthropic.com"), "claude-x", messages, {"reasoning_effort": "low"})
    body = sent[-1]
    assert body["output_config"] == {"effort": "low"} and "reasoning_effort" not in body
    # Кеш промпта: метка на системном промпте и автоматическая — на хвосте разговора.
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["system"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]


async def test_claude_through_gateway_caches_system_prompt(monkeypatch):
    messages = [{"role": "system", "content": "big system"}, {"role": "system", "content": "summary"},
                {"role": "user", "content": "hi"}]
    sent = _capture(monkeypatch, provider_response(False, text="ok"))
    await _one(_provider(base_url="https://openrouter.ai/api/v1"), "anthropic/claude-sonnet", messages, {})
    first, last_system, user = sent[-1]["messages"]
    assert first["content"] == "big system"
    assert last_system["content"] == [{"type": "text", "text": "summary", "cache_control": {"type": "ephemeral"}}]
    assert user["content"] == "hi"
    await _one(_provider(base_url="https://openrouter.ai/api/v1"), "anthropic/claude-sonnet", messages,
               {"drop": ["cache_control"]})
    assert sent[-1]["messages"][1]["content"] == "summary"
    await _one(_provider(), "gpt-x", messages, {})
    assert sent[-1]["messages"][1]["content"] == "summary"  # не Claude — без меток


def test_rejected_params_are_recognised_by_whole_name():
    reasoning_content = classify_rejection(400, "messages.1.reasoning_content: Extra inputs are not permitted")
    assert reasoning_content.capability == "replay_reasoning"  # не путается с параметром reasoning
    effort = classify_rejection(400, '{"error": {"message": "output_config.effort: Extra inputs are not permitted"}}')
    assert (effort.capability, effort.value) == ("drop", "reasoning_effort")
    cache = classify_rejection(400, '{"error": {"message": "cache_control: Extra inputs are not permitted"}}')
    assert cache.value == "cache_control"
    budget = classify_rejection(400, '{"error": {"message": "Unrecognized request argument: thinking_budget"}}')
    assert budget.value == "thinking_budget"


async def test_reasoning_budget_setting_round_trip(client):
    provider_id = await _setup(client)
    assert (await _settings(client, provider_id, reasoning_budget=4096))["reasoning_budget"] == 4096
    assert (await _settings(client, provider_id, reasoning_budget=0))["reasoning_budget"] == 0
    assert (await _settings(client, provider_id))["reasoning_budget"] is None


# --- План, правила проекта, ревью, принципы дизайна ---------------------------------

def _todo(items, ident="t1"):
    return ("tool_calls", [{"id": ident, "type": "function", "function": {
        "name": "update_todos", "arguments": json.dumps({"todos": items})}}])


async def test_todo_plan_is_shown_as_checklist_and_remembered(client, monkeypatch, db_sessionmaker):
    await _setup(client)
    calls: list = []
    _script(monkeypatch, [
        [_todo([{"content": "Шапка", "status": "in_progress"}, {"content": "Футер"}])],
        [_todo([{"content": "Шапка", "status": "done"}, {"content": "Футер", "status": "done"}], "t2")],
        [("content", "Готово")],
    ], calls)
    chat, state = await _run(client)
    assert state["status"] == "done", state
    assert any(s["text"] == "План: 0 из 2 · Шапка" for s in state["steps"])
    answer = (await client.get(f"/api/chats/{chat['id']}")).json()["messages"][-1]
    assert answer["meta"]["todos"] == [{"content": "Шапка", "status": "done"}, {"content": "Футер", "status": "done"}]
    assert answer["meta"]["tools"] == []  # план — не карточка инструмента
    tool_result = json.loads(calls[1]["conversation"][-1]["content"])
    assert tool_result == {"ok": True, "note": "Plan saved: 0/2 done."}
    async with db_sessionmaker() as s:
        row = await s.get(Message, answer["id"])
        assert "[Task list at the end of this turn: ✓ Шапка; ✓ Футер]" in history.entry(row)["content"]


async def test_project_rules_reach_the_prompt(client, monkeypatch, db_sessionmaker):
    await _setup(client)
    calls: list = []
    _script(monkeypatch, [[("content", "ok")]], calls)
    chat, _ = await _run(client, domain="code")
    assert "Project rules from LAYLA.md" not in calls[0]["conversation"][0]["content"]
    assert "LAYLA.md" in calls[0]["conversation"][0]["content"]  # модель знает, куда записывать правила
    project_id = (await client.get(f"/api/chats/{chat['id']}")).json()["project_id"]
    async with db_sessionmaker() as s:
        root = Path((await s.get(Project, project_id)).path)
    (root / "LAYLA.md").write_text("- Всегда Tailwind\n- Тексты на «вы»\n")
    job = (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "ещё"})).json()
    for _ in range(300):
        await asyncio.sleep(0.02)
        if (await client.get(f"/api/jobs/{job['id']}")).json()["status"] not in ("queued", "running"):
            break
    system = calls[-1]["conversation"][0]["content"]
    assert "Project rules from LAYLA.md" in system and "Всегда Tailwind" in system


def test_rules_file_helper(tmp_path):
    assert project_agent.project_rules(str(tmp_path)) == ""
    (tmp_path / "LAYLA.md").write_text("   \n")
    assert project_agent.project_rules(str(tmp_path)) == ""
    (tmp_path / "LAYLA.md").write_text("- правило\n" + "x" * 30_000)
    rules = project_agent.project_rules(str(tmp_path))
    assert rules.startswith("Project rules from LAYLA.md") and len(rules) < 21_000


async def test_review_mode_is_read_only(client, monkeypatch):
    await _setup(client)
    calls: list = []
    _script(monkeypatch, [[("content", "1. [важно] a.py:3 …")]], calls)
    _, state = await _run(client, domain="code", mode="review")
    assert state["status"] == "done", state
    assert calls[0]["tools"] == {"list_files", "read_file", "update_todos"}
    assert "REVIEW MODE" in calls[0]["conversation"][0]["content"]


async def test_design_prompts_carry_principles_and_short_thinking(client, monkeypatch):
    system = design_gen.build_prompt({}, "html")[0]["content"]
    assert "Принципы дизайна" in system and "Размышляй коротко" in system
    await _setup(client)
    calls: list = []
    _script(monkeypatch, [[("content", "ok")]], calls)
    await _run(client, domain="design")
    prompt = calls[0]["conversation"][0]["content"]
    assert "Принципы дизайна" in prompt and "edit_file маленькими фрагментами" in prompt
    assert "Keep your private reasoning brief" in prompt
    calls.clear()
    await _run(client, domain="osint")
    assert "Принципы дизайна" not in calls[0]["conversation"][0]["content"]
