"""Сжатие контекста: ужатие длинного хода и ручная сводка истории."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services import history
from tests.test_provider_resilience import _chat_with_history, _http, _setup, _wire


def _round(name, args, result, call_id):
    call = {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    return [{"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result)}]


def test_long_run_shrinks_old_tool_results_but_keeps_recent_rounds():
    anchor = {"role": "user", "content": "сделай сайт"}
    big = "x" * 30_000
    conversation = [{"role": "system", "content": "sys"}, anchor]
    conversation += _round("write_file", {"path": "a.html", "content": big, "expected_sha256": None},
                           {"change": {"path": "a.html"}}, "c1")
    conversation += _round("read_file", {"path": "a.html"}, {"path": "a.html", "content": big, "sha256": "s1"}, "c2")
    conversation += _round("read_file", {"path": "b.html"}, {"path": "b.html", "content": big, "sha256": "s2"}, "c3")
    conversation += _round("read_file", {"path": "c.html"}, {"path": "c.html", "content": big, "sha256": "s3"}, "c4")
    shrunk = history.shrink_run(conversation, anchor, {"context": 32_000, "max_output": 4096})
    assert shrunk >= 2
    written = json.loads(conversation[2]["tool_calls"][0]["function"]["arguments"])
    assert written["content"] == "[30000 символов — уже применено]" and written["path"] == "a.html"
    assert json.loads(conversation[5]["content"]) == {"path": "a.html", "sha256": "s1", "note": history._HIDDEN}
    # Последние два шага — целиком: модель работает с ними прямо сейчас.
    assert json.loads(conversation[-1]["content"])["content"] == big
    assert json.loads(conversation[-3]["content"])["content"] == big
    assert conversation[1] is anchor


def test_short_run_is_untouched():
    anchor = {"role": "user", "content": "привет"}
    conversation = [anchor, *_round("read_file", {"path": "a"}, {"path": "a", "content": "hi", "sha256": "s"}, "c1")]
    before = json.dumps(conversation)
    assert history.shrink_run(conversation, anchor, {}) == 0
    assert json.dumps(conversation) == before


async def _wait(client, job_id):
    for _ in range(300):
        await asyncio.sleep(0.02)
        state = (await client.get(f"/api/jobs/{job_id}")).json()
        if state["status"] not in ("queued", "running"):
            return state
    return state


@pytest.mark.asyncio
async def test_manual_compaction_keeps_recent_messages(client, monkeypatch, db_sessionmaker):
    await _setup(client)
    chat = await _chat_with_history(client, db_sessionmaker, turns=10, size=80)
    await client.patch(f"/api/chats/{chat['id']}", json={"title": "Сайт"})
    sent: list = []
    _http(monkeypatch, [_wire(text="- пользователь делает сайт")], sent)
    job = (await client.post(f"/api/chats/{chat['id']}/compact")).json()
    state = await _wait(client, job["id"])
    assert state["status"] == "done", state
    assert state["result"]["count"] == 6
    messages = (await client.get(f"/api/chats/{chat['id']}")).json()["messages"]
    assert [m["role"] for m in messages].count("system") == 1
    assert messages[6]["meta"]["kind"] == "summary" and messages[6]["content"] == "- пользователь делает сайт"
    assert "#0" in sent[0]["messages"][1]["content"] and "#6" not in sent[0]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_nothing_to_compact_in_short_chat(client, monkeypatch, db_sessionmaker):
    await _setup(client)
    chat = await _chat_with_history(client, db_sessionmaker, turns=3, size=40)
    sent: list = []
    _http(monkeypatch, [_wire(text="сводка")], sent)
    state = await _wait(client, (await client.post(f"/api/chats/{chat['id']}/compact")).json()["id"])
    assert state["status"] == "done" and sent == []
    assert "Сжимать нечего" in state["steps"][-1]["text"]
