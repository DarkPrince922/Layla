"""The lead replays actual tool outcomes, with atomic mail and reversible context."""
import asyncio
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.chat import Chat, Message
from app.models.code_session import CodeLeadEntry, CodeLeadTurn
from app.models.code_team import CodeTeamMessage
from app.services import code_sessions, code_teams, history, project_agent
from tests.test_code_runner import _script, _wait
from tests.test_code_workers import setup


async def run(client, chat, text):
    response = await client.post(f"/api/chats/{chat['id']}/run", json={"content": text})
    assert response.status_code == 202, response.text
    state = await _wait(client, response.json()["id"], lambda row: row["status"] not in ("queued", "running"))
    assert state["status"] == "done", state
    return (await client.get(f"/api/chats/{chat['id']}")).json()["messages"][-1]


async def test_lead_recovers_protocol_after_cancel_without_repeating_write(client, db_sessionmaker, monkeypatch):
    manager, chat, root = await setup(client, db_sessionmaker)
    parked = asyncio.Event()
    seen = []
    count = 0

    async def turn(_p, _k, _m, conversation, _tools, _caps, _anchor):
        nonlocal count
        count += 1
        seen.append(json.loads(json.dumps(conversation)))
        if count == 1:
            yield "done", ([], [], {}, [{"id": "write-once", "type": "function", "function": {
                "name": "write_file", "arguments": '{"path":"once.txt","content":"saved","expected_sha256":null}'}}])
        elif count == 2:
            parked.set()
            await asyncio.Event().wait()
        else:
            assert any(item.get("tool_call_id") == "write-once" and '"change"' in item.get("content", "") for item in conversation)
            yield "delta", "Recovered"
            yield "done", (["Recovered"], [], {}, [])

    monkeypatch.setattr(project_agent, "_turn", turn)
    response = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "Write a file and verify it"})
    await asyncio.wait_for(parked.wait(), 5)
    assert (await client.post(f"/api/jobs/{response.json()['id']}/cancel")).status_code == 200
    assert (root / "once.txt").read_text() == "saved"
    await run(client, chat, "Continue the interrupted task")
    journal = (await client.get(f"/api/chats/{chat['id']}/session?limit=100")).json()["entries"]
    assert sum("tool_calls" in row["message"] for row in journal) == 1
    assert any(row["status"] == "interrupted" for row in journal)
    assert journal[-1]["message"]["content"] == "Recovered"
    other = (await client.post("/api/chats", json={"domain": "code", "project_id": manager.project_id, "model": "m"})).json()
    assert not (await client.get(f"/api/chats/{other['id']}/session")).json()["entries"]
    await client.post("/api/auth/logout")
    await client.post("/api/auth/register", json={"email": "other-lead@example.com", "password": "hunter2hunter2"})
    assert (await client.get(f"/api/chats/{chat['id']}/session")).status_code == 404


async def test_rollback_disables_replay_but_keeps_original_journal(client, db_sessionmaker, monkeypatch):
    _manager, chat, root = await setup(client, db_sessionmaker)
    _script(monkeypatch, [[{"name": "write_file", "args": {"path": "one.txt", "content": "one", "expected_sha256": None}}]])
    first = await run(client, chat, "First change")
    _script(monkeypatch, [[{"name": "write_file", "args": {"path": "two.txt", "content": "two", "expected_sha256": None}}]])
    second = await run(client, chat, "Second change")
    result = await client.post(f"/api/chats/{chat['id']}/messages/{second['id']}/rollback")
    assert result.status_code == 200, result.text
    seen = []
    _script(monkeypatch, [], seen)
    await run(client, chat, "What remains after rollback?")
    assert "one.txt" in json.dumps(seen[0]["conversation"])
    assert "two.txt" not in json.dumps(seen[0]["conversation"])
    async with db_sessionmaker() as session:
        assert (await session.get(CodeLeadTurn, first["id"])).active
        assert not (await session.get(CodeLeadTurn, second["id"])).active
        assert list(await session.scalars(select(CodeLeadEntry).where(CodeLeadEntry.turn_id == second["id"])))
    assert (root / "one.txt").exists() and not (root / "two.txt").exists()


async def test_compaction_uses_tool_results_and_preserves_raw_rows(client, db_sessionmaker, monkeypatch):
    _manager, chat, root = await setup(client, db_sessionmaker)
    (root / "facts.txt").write_text("VERIFIED-TOOL-FACT")
    _script(monkeypatch, [[{"name": "read_file", "args": {"path": "facts.txt"}}]])
    await run(client, chat, "Read the real evidence")
    captured = []

    async def summarize(_p, _k, _m, _caps, _previous, items, **_):
        captured.extend(items)
        return "Compact verified facts"

    monkeypatch.setattr(history, "summarize", summarize)
    async with db_sessionmaker() as session:
        row = await session.get(Chat, chat["id"])
        await history.compact(session, row, None, None, "m", {}, keep=0)
        count = len(list(await session.scalars(select(CodeLeadEntry))))
    assert "VERIFIED-TOOL-FACT" in json.dumps(captured)
    seen = []
    _script(monkeypatch, [], seen)
    await run(client, chat, "Continue with the compact view")
    context = seen[0]["conversation"]
    assert "Compact verified facts" in json.dumps(context)
    assert "VERIFIED-TOOL-FACT" not in json.dumps(context)
    async with db_sessionmaker() as session:
        rows = list(await session.scalars(select(CodeLeadEntry)))
        assert len(rows) > count and any("VERIFIED-TOOL-FACT" in json.dumps(row.message) for row in rows)


async def test_lead_mail_ack_and_journal_commit_together(client, db_sessionmaker):
    manager, chat, _root = await setup(client, db_sessionmaker)
    await manager.team.create(code_teams.Create(team_name="mail"))
    async with db_sessionmaker() as session:
        source = Message(chat_id=chat["id"], role="user", content="Original task", created_at=datetime.now(UTC))
        output = Message(chat_id=chat["id"], role="assistant", content="", created_at=datetime.now(UTC))
        session.add_all([source, output])
        await session.commit()
    recorder = code_sessions.LeadTranscript(manager.runtime, output.id, source.id, manager.team)
    messages = await recorder.load([{"role": "user", "content": source.content}])
    async with db_sessionmaker() as session:
        team = (await manager.team.info())["team"]
        session.add(CodeTeamMessage(team_id=team["id"], sequence=1, sender="peer", recipient="lead",
                                    content="Important result", summary="Result"))
        session.add(CodeLeadEntry(turn_id=output.id, sequence=recorder.sequence, message={"role": "user", "content": "collision"}))
        await session.commit()
    conversation = recorder.conversation(messages)
    with pytest.raises(IntegrityError):
        await recorder.flush(conversation, receive=True)
    async with db_sessionmaker() as session:
        assert not (await session.scalar(select(CodeTeamMessage))).delivered


async def test_unfinished_tool_is_repaired_with_unknown_outcome_and_fresh_system(client, db_sessionmaker):
    manager, chat, _root = await setup(client, db_sessionmaker)
    async with db_sessionmaker() as session:
        source = Message(chat_id=chat["id"], role="user", content="Original", created_at=datetime.now(UTC))
        output = Message(chat_id=chat["id"], role="assistant", content="", created_at=datetime.now(UTC))
        session.add_all([source, output])
        await session.commit()
    first = code_sessions.LeadTranscript(manager.runtime, output.id, source.id, provider_id="old", model="a")
    messages = await first.load([{"role": "system", "content": "old persona"}, {"role": "user", "content": "Original"}])
    conversation = first.conversation(messages)
    conversation.append({"role": "assistant", "content": "", "_anthropic_thinking": [{"type": "thinking", "thinking": "provider-specific", "signature": "opaque"}], "tool_calls": [{"id": "unknown", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]})
    await first.flush(conversation)
    async with db_sessionmaker() as session:
        source2 = Message(chat_id=chat["id"], role="user", content="Continue", created_at=datetime.now(UTC))
        output2 = Message(chat_id=chat["id"], role="assistant", content="", created_at=datetime.now(UTC))
        session.add_all([source2, output2])
        await session.commit()
    restored = code_sessions.LeadTranscript(manager.runtime, output2.id, source2.id, provider_id="new", model="b")
    replay = await restored.load([{"role": "system", "content": "new persona"}, {"role": "user", "content": "Continue"}])
    assert replay[0]["content"] == "new persona" and "old persona" not in json.dumps(replay)
    assert any(item.get("tool_call_id") == "unknown" and "Outcome unknown" in item["content"] for item in replay)
    assert not any("_anthropic_thinking" in item for item in replay)


def test_context_trimming_keeps_tool_calls_and_results_in_atomic_groups():
    anchor = {"role": "user", "content": "current task"}
    conversation = [{"role": "system", "content": "policy"}, {"role": "user", "content": "old " * 2000},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "a", "content": "first"},
        {"role": "tool", "tool_call_id": "b", "content": "second"},
        {"role": "user", "content": "recent task"}, {"role": "assistant", "content": "recent result"}, anchor]
    assert project_agent.fit_context(conversation, anchor, {"context": 500, "max_output": 100})
    pending = set()
    for message in conversation:
        if message["role"] == "assistant":
            pending.update(call["id"] for call in message.get("tool_calls", []))
        elif message["role"] == "tool":
            assert message["tool_call_id"] in pending
            pending.remove(message["tool_call_id"])
    assert not pending and anchor in conversation
