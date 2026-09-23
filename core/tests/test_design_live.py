"""Генерация по брифу: настройки модели, живой черновик и расширенный бриф."""
from __future__ import annotations

import asyncio

import pytest

from app.services import design_gen, tool_chat
from app.services.provider_errors import CapabilityError, OutputLimitError

pytestmark = pytest.mark.asyncio


async def _setup(client):
    await client.post("/api/auth/register", json={"email": "live@example.com", "password": "hunter2hunter2"})
    await client.post("/api/providers", json={"name": "M", "kind": "openai_compatible",
                                              "base_url": "http://p/v1", "default_model": "m", "active": True})
    return (await client.get("/api/providers")).json()[0]["id"]


async def _settings(client, provider_id, **fields):
    body = {"name": "m", "enabled": True, "tools": True, **fields}
    assert (await client.put(f"/api/providers/{provider_id}/models", json={"models": [body]})).status_code == 200


def _fake(monkeypatch, steps: list, calls: list):
    """steps[i] — ответ i-й попытки: строка, исключение или (кусок, исключение)."""
    async def stream_turn(provider, key, model, messages, tools, caps=None):
        calls.append({"caps": dict(caps or {}), "messages": messages, "tools": tools})
        step = steps[min(len(calls), len(steps)) - 1]
        if isinstance(step, tuple):
            yield ("content", step[0])
            raise step[1]
        if isinstance(step, BaseException):
            raise step
        yield ("reasoning", "думаю о сетке")
        yield ("content", step)

    monkeypatch.setattr(tool_chat, "stream_turn", stream_turn)


async def _generate(client, brief=None, **extra):
    r = await client.post("/api/designs/generate", json={"stack": "html", "brief": brief or {}, **extra})
    assert r.status_code == 202, r.text
    return r.json()["id"]


async def _wait(client, job_id):
    for _ in range(300):
        await asyncio.sleep(0.02)
        state = (await client.get(f"/api/jobs/{job_id}")).json()
        if state["status"] not in ("queued", "running"):
            return state
    return state


async def test_model_settings_apply_to_design(client, monkeypatch):
    provider_id = await _setup(client)
    await _settings(client, provider_id, temperature=0.3, reasoning_effort="high",
                    max_output=12000, max_output_manual=True)
    calls: list = []
    _fake(monkeypatch, ["```html\n<h1>x</h1>\n```"], calls)
    state = await _wait(client, await _generate(client))
    assert state["status"] == "done", state
    caps = calls[0]["caps"]
    assert (caps["temperature"], caps["reasoning_effort"], caps["max_output"]) == (0.3, "high", 12000)
    assert calls[0]["tools"] == []  # макет — один ответ, без инструментов
    assert "думаю о сетке" in state["reasoning"]


async def test_auto_output_limit_is_raised_for_design(client, monkeypatch):
    await _setup(client)
    calls: list = []
    _fake(monkeypatch, ["```html\n<h1>x</h1>\n```"], calls)
    await _wait(client, await _generate(client))
    assert calls[0]["caps"]["max_output"] == design_gen.DESIGN_MAX_OUTPUT
    assert "temperature" not in calls[0]["caps"]  # «Сбалансировано» не трогает температуру


async def test_creativity_sets_temperature_for_this_generation(client, monkeypatch):
    provider_id = await _setup(client)
    await _settings(client, provider_id, temperature=0.2)
    calls: list = []
    _fake(monkeypatch, ["```html\n<h1>x</h1>\n```"], calls)
    await _wait(client, await _generate(client, {"creativity": "wild"}))
    assert calls[0]["caps"]["temperature"] == 1.0
    # В настройках модели осталось своё значение.
    assert (await client.get(f"/api/providers/{provider_id}/models")).json()[0]["temperature"] == 0.2


async def test_rejected_param_is_learned_by_design(client, monkeypatch):
    provider_id = await _setup(client)
    calls: list = []
    _fake(monkeypatch, [CapabilityError("no temperature", capability="drop", value="temperature"),
                        "```html\n<h1>ok</h1>\n```"], calls)
    state = await _wait(client, await _generate(client, {"creativity": "bold"}))
    assert state["status"] == "done", state
    assert calls[1]["caps"]["drop"] == ["temperature"]
    assert (await client.get(f"/api/providers/{provider_id}/models")).json()[0]["dropped"] == ["temperature"]


async def test_draft_is_visible_while_model_writes(client, monkeypatch):
    await _setup(client)
    release = asyncio.Event()

    async def stream_turn(provider, key, model, messages, tools, caps=None):
        yield ("reasoning", "раскладываю секции")
        yield ("content", "```html\n<!doctype html><style>body{margin:0}</style><h1>Живой")
        await asyncio.wait_for(release.wait(), 10)
        yield ("content", " макет</h1>\n```")

    monkeypatch.setattr(tool_chat, "stream_turn", stream_turn)
    job_id = await _generate(client)
    draft = ""
    for _ in range(300):
        await asyncio.sleep(0.02)
        state = (await client.get(f"/api/jobs/{job_id}")).json()
        draft = state["result"].get("draft") or ""
        if "Живой" in draft:
            break
    assert "Живой" in draft and state["status"] == "running"
    assert state["result"]["model"] == "m"
    assert "раскладываю" in state["reasoning"]
    # В общем списке задач черновика нет — он большой.
    listed = next(j for j in (await client.get("/api/jobs")).json() if j["id"] == job_id)
    assert "draft" not in listed["result"]
    release.set()
    state = await _wait(client, job_id)
    assert state["status"] == "done" and state["result"]["draft"] is None
    design = (await client.get(f"/api/designs/{state['result']['design_id']}")).json()
    assert design["files"][0]["content"].endswith("<h1>Живой макет</h1>")


async def test_truncated_design_is_saved_with_note(client, monkeypatch):
    provider_id = await _setup(client)
    await _settings(client, provider_id, max_output=16384, max_output_manual=True)
    calls: list = []
    _fake(monkeypatch, [("```html\n<main><h1>Длинный заголовок", OutputLimitError("limit", has_calls=False))], calls)
    state = await _wait(client, await _generate(client))
    assert state["status"] == "done", state
    # Модель каждый раз обрывается — после всех продолжений сохраняем, что есть, без повторов.
    assert len(calls) == 1 + design_gen.MAX_CONTINUATIONS and state["result"]["truncated"] is True
    design = (await client.get(f"/api/designs/{state['result']['design_id']}")).json()
    assert design["files"][0]["content"] == "<main><h1>Длинный заголовок"
    assert any("предельную длину" in s["text"] for s in state["steps"])


async def test_long_design_is_continued_where_it_stopped(client, monkeypatch):
    provider_id = await _setup(client)
    await _settings(client, provider_id, max_output=16384, max_output_manual=True)
    calls: list = []
    _fake(monkeypatch, [("```html\n<main><h1>Начало", OutputLimitError("limit", has_calls=False)),
                        "```html\n</h1><p>конец</p></main>\n```"], calls)
    state = await _wait(client, await _generate(client))
    assert state["status"] == "done", state
    assert len(calls) == 2 and state["result"]["truncated"] is False
    follow_up = calls[1]["messages"]
    assert follow_up[-2] == {"role": "assistant", "content": "```html\n<main><h1>Начало"}
    assert follow_up[-1]["content"] == design_gen.CONTINUE_PROMPT
    design = (await client.get(f"/api/designs/{state['result']['design_id']}")).json()
    assert design["files"][0]["content"] == "<main><h1>Начало</h1><p>конец</p></main>"
    assert any("часть 2" in s["text"] for s in state["steps"])


def test_continuation_drops_reopened_block_and_overlap():
    base = "```html\n<section class=\"hero\"><h1>Заголовок</h1>"
    assert design_gen.join_continuation(base, "```html\n<p>дальше</p>") == base + "<p>дальше</p>"
    repeated = '<section class="hero"><h1>Заголовок</h1><p>дальше</p>'
    assert design_gen.join_continuation(base, repeated) == base + "<p>дальше</p>"


async def test_output_limit_grows_and_is_remembered(client, monkeypatch):
    provider_id = await _setup(client)
    calls: list = []
    _fake(monkeypatch, [("```html\n<h1>нач", OutputLimitError("limit", has_calls=False)), "```html\n<h1>весь</h1>\n```"], calls)
    state = await _wait(client, await _generate(client))
    assert state["status"] == "done", state
    assert [c["caps"]["max_output"] for c in calls] == [16384, 32768]
    assert (await client.get(f"/api/providers/{provider_id}/models")).json()[0]["max_output"] == 32768
    design = (await client.get(f"/api/designs/{state['result']['design_id']}")).json()
    assert design["files"][0]["content"] == "<h1>весь</h1>"


async def test_empty_answer_is_an_error(client, monkeypatch):
    await _setup(client)
    _fake(monkeypatch, ["```html\n```"], [])
    state = await _wait(client, await _generate(client))
    assert state["status"] == "error" and "разметку" in state["error"]
    assert (await client.get("/api/designs")).json() == []


async def test_extended_brief_reaches_prompt(client, monkeypatch):
    await _setup(client)
    calls: list = []
    _fake(monkeypatch, ["```html\n<h1>x</h1>\n```"], calls)
    brief = {
        "artifact_type": "Портфолио", "direction": "Swiss", "theme": "dark", "brand": "Studio Nord",
        "industry": "архитектура", "audience": "девелоперы", "language": "English",
        "palette": "Монохром", "accent_color": "#FF5500", "fonts": "Space Grotesk",
        "effects": ["Зерно", "Стекло"], "sections": ["Hero", "Проекты", "Контакты"],
        "animation": "выразительные", "accessibility": True, "creativity": "bold",
    }
    state = await _wait(client, await _generate(client, brief))
    assert state["status"] == "done", state
    prompt = calls[0]["messages"][1]["content"]
    for text in ("Портфолио", "Studio Nord", "архитектура", "девелоперы", "English", "#FF5500",
                 "Space Grotesk", "Зерно, Стекло", "Hero, Проекты, Контакты", "WCAG", "Смело", "тёмная"):
        assert text in prompt, text
    saved = (await client.get("/api/designs")).json()[0]["brief"]
    assert saved["sections"] == ["Hero", "Проекты", "Контакты"] and saved["creativity"] == "bold"


async def test_brief_is_validated(client):
    await _setup(client)
    for bad in ({"accent_color": "red"}, {"creativity": "insane"}, {"sections": ["x"] * 30}):
        r = await client.post("/api/designs/generate", json={"stack": "html", "brief": bad})
        assert r.status_code == 422, bad


def test_extract_html_from_unclosed_block():
    assert design_gen.extract_html("Вот:\n```html\n<main><h1>Обрыв") == "<main><h1>Обрыв"
    assert design_gen.extract_html("```\n<p>a</p>\n```") == "<p>a</p>"


def test_design_caps_respect_known_limit():
    assert design_gen.design_caps({"max_output": 4096, "max_output_cap": 8000}, {})["max_output"] == 8000
    assert design_gen.design_caps({"max_output": 40000}, {})["max_output"] == 40000
    manual = {"max_output": 2048, "max_output_cap": 2048, "max_output_manual": True}
    assert design_gen.design_caps(manual, {"creativity": "safe"}) == {**manual, "temperature": 0.35}
