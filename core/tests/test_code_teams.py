"""Durable internal mail, real worker recovery and account/chat boundaries."""
from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.code_team import CodeTeamMessage, CodeTranscript
from app.models.code_worker import CodeWorker
from app.services import (
    code_sessions,
    code_teams,
    code_workers,
    files,
    project_agent,
    sandbox,
)
from tests.test_code_workers import setup, terminal


async def no_sandbox():
    return None


async def dormant_team(client, maker, monkeypatch):
    manager, chat, root = await setup(client, maker)
    monkeypatch.setattr(code_workers.jobs, "launch", lambda *_: None)
    assert "error" not in await manager.team.call("team_create", {"team_name": "builders"})
    ids = []
    for name in ("builder", "reviewer"):
        ids.append((await manager.start_agent(code_workers.AgentStart(
            task="Inspect and complete the assignment", name=name, detached=True)))["worker"]["id"])
    return manager, chat, root, ids


async def test_mail_routes_broadcast_and_archives_without_cross_chat_access(client, db_sessionmaker, monkeypatch):
    manager, chat, _root, ids = await dormant_team(client, db_sessionmaker, monkeypatch)
    peer = code_teams.Runtime(manager, ids[0])
    assert "error" not in await peer.call("send_message", {"to": "reviewer", "message": "Check the parser"})
    assert "error" not in await manager.team.call("send_message", {"to": "*", "message": "Run tests after changes"})
    state = await manager.team.info()
    assert [(m["from"], m["to"]) for m in state["messages"]] == [("builder", "reviewer"), ("lead", "builder"), ("lead", "reviewer")]
    assert [m["to"] for m in (await peer.info())["messages"]] == ["reviewer", "builder"]
    assert "error" in await manager.team.call("send_message", {"to": "outsider", "message": "No"})
    assert "error" in await peer.call("team_delete", {})
    other = (await client.post("/api/chats", json={"domain": "code", "project_id": manager.project_id, "model": "m"})).json()
    team_id = state["team"]["id"]
    assert (await client.get(f"/api/chats/{other['id']}/team?team_id={team_id}")).status_code == 404
    assert (await client.get(f"/api/chats/{other['id']}/workers/{ids[0]}/transcript")).status_code == 404
    assert (await client.post(f"/api/chats/{other['id']}/team/message", json={"to": "builder", "message": "No"})).status_code == 409
    # Live team cleanup must not delete worktrees or silently terminate peers.
    async with db_sessionmaker() as session:
        rows = list(await session.scalars(select(CodeWorker).where(CodeWorker.id.in_(ids))))
        for row in rows:
            row.status, row.slot = "running", None
        await session.commit()
    monkeypatch.setattr(manager, "list", lambda: asyncio.sleep(0, result=[]))
    assert "error" in await manager.team.call("team_delete", {})
    async with db_sessionmaker() as session:
        for ident in ids:
            row = await session.get(CodeWorker, ident)
            row.status = "interrupted"
        await session.commit()
    assert "closed" in await manager.team.call("team_delete", {})
    archived = (await client.get(f"/api/chats/{chat['id']}/team?team_id={team_id}")).json()
    assert not archived["team"]["active"] and len(archived["messages"]) == 3
    assert "error" in await peer.call("send_message", {"to": "lead", "message": "Closed"})
    with pytest.raises(ValueError, match="закрыта"):
        await manager.resume(ids[0])
    assert "error" not in await manager.team.call("team_create", {"team_name": "builders"})
    # Another account cannot inspect either the team's metadata or its transcript.
    await client.post("/api/auth/logout")
    await client.post("/api/auth/register", json={"email": "other-team@example.com", "password": "hunter2hunter2"})
    assert (await client.get(f"/api/chats/{chat['id']}/team")).status_code == 404
    assert (await client.get(f"/api/chats/{chat['id']}/workers/{ids[0]}/transcript")).status_code == 404


async def test_unique_names_and_read_only_modes_cannot_mutate_team(client, db_sessionmaker, monkeypatch):
    manager, _chat, _root, _ids = await dormant_team(client, db_sessionmaker, monkeypatch)
    for name in ("builder", "lead"):
        with pytest.raises(ValueError, match="занято"):
            await manager.start_agent(code_workers.AgentStart(task="Inspect another component", name=name))
    assert "error" in await manager.team.call("team_create", {"team_name": "second"})
    for mode in ("plan", "review"):
        restricted = code_workers.Manager(manager.runtime, manager.root, mode=mode)
        assert {t["function"]["name"] for t in restricted.team.tools()} == {"team_info"}
        assert "error" in await restricted.team.call("send_message", {"to": "builder", "message": "Do this"})
    restricted = code_workers.Manager(manager.runtime, manager.root, permissions=[])
    assert restricted.team.tools() == []
    with pytest.raises(ValueError, match="Персона"):
        await restricted.team.info()


async def test_worker_receives_mail_and_resumes_full_tool_history(client, db_sessionmaker, monkeypatch):
    manager, chat, root = await setup(client, db_sessionmaker)
    monkeypatch.setattr(sandbox, "info", no_sandbox)
    await manager.team.create(code_teams.Create(team_name="repair"))
    parked, resumed = asyncio.Event(), False
    observed = []

    async def turn(_provider, _key, _model, conversation, tools, _caps, _anchor):
        nonlocal resumed
        observed.append(json.loads(json.dumps(conversation)))
        names = {tool["function"]["name"] for tool in tools}
        assert "send_message" in names and "agent_start" not in names
        if not any(m.get("role") == "tool" for m in conversation):
            yield "done", ([], [], {}, [{"id": "create", "type": "function", "function": {
                "name": "write_file", "arguments": json.dumps({"path": "answer.txt", "content": "verified", "expected_sha256": None})}}])
        elif not resumed:
            parked.set()
            await asyncio.Event().wait()
        else:
            assert any(m.get("role") == "tool" and '"change"' in m.get("content", "") for m in conversation)
            assert any("Use the new acceptance test" in m.get("content", "") for m in conversation)
            yield "delta", "Recovered without repeating file writes"
            yield "done", (["Recovered without repeating file writes"], [], {}, [])

    monkeypatch.setattr(project_agent, "_turn", turn)
    ident = (await manager.start_agent(code_workers.AgentStart(task="Implement and verify answer", name="builder", detached=True)))["worker"]["id"]
    await asyncio.wait_for(parked.wait(), 5)
    await manager.stop(ident)
    assert not (root / "answer.txt").exists()
    # Reconstruct the manager just as after a server restart/navigation.
    fresh = code_workers.Manager(manager.runtime, manager.root, provider_id=manager.provider_id, model="m")
    await fresh.team.send(code_teams.Send(to="builder", message="Use the new acceptance test"))
    resumed = True
    await fresh.resume(ident)
    result = await terminal(fresh, ident)
    assert result["worker"]["status"] == "done", result
    assert len(result["worker"]["changes"]) == 1
    page = (await client.get(f"/api/chats/{chat['id']}/workers/{ident}/transcript?limit=2")).json()
    assert len(page["entries"]) == 2 and page["next_offset"] == 2
    async with db_sessionmaker() as session:
        rows = list(await session.scalars(select(CodeTranscript).where(CodeTranscript.worker_id == ident).order_by(CodeTranscript.sequence)))
        assert sum("tool_calls" in row.message for row in rows) == 1
        assert rows[-1].message["role"] == "assistant"
        assert rows[-1].message["content"] == "Recovered without repeating file writes"
        mail = await session.scalar(select(CodeTeamMessage))
        assert mail.delivered
    # Done workers need new mail before they may resume, keeping the same worktree.
    with pytest.raises(ValueError, match="отправить"):
        await fresh.resume(ident)
    await fresh.team.send(code_teams.Send(to="builder", message="Use the new acceptance test again"))
    await fresh.resume(ident)
    assert (await terminal(fresh, ident))["worker"]["status"] == "done"
    assert len(observed[-1]) > len(observed[0])


async def test_interrupted_side_effect_is_not_replayed_and_compaction_keeps_journal(client, db_sessionmaker, monkeypatch):
    manager, _chat, root, ids = await dormant_team(client, db_sessionmaker, monkeypatch)
    monkeypatch.setattr(sandbox, "info", no_sandbox)
    transcript = code_sessions.Transcript(manager, ids[0], code_teams.Runtime(manager, ids[0]))
    messages = await transcript.load("Write one file")
    crashed = False
    flush = transcript.flush

    async def before_crash(*args, **kwargs):
        if not crashed:
            return await flush(*args, **kwargs)

    monkeypatch.setattr(transcript, "flush", before_crash)

    async def turn(*_):
        yield "done", ([], [], {}, [{"id": "uncertain", "type": "function", "function": {
            "name": "write_file", "arguments": '{"path":"once.txt","content":"once","expected_sha256":null}'}}])

    monkeypatch.setattr(project_agent, "_turn", turn)
    # Simulate a hard process crash after a side effect, before the next DB flush.
    async with aclosing(project_agent.run(SimpleNamespace(), "", "m", messages, str(root),
                                         worker_context=True, transcript=transcript)) as stream:
        async for event in stream:
            if event.get("tool", {}).get("status") == "done":
                crashed = True
                break
    assert files.read_file(root, "once.txt")["content"] == "once"
    restored = code_sessions.Transcript(manager, ids[0])
    history = await restored.load("Original")
    assert any(m.get("tool_call_id") == "uncertain" and "Outcome unknown" in m["content"] for m in history)
    conversation = restored.conversation([{"role": "system", "content": "current policy"}, *history])
    conversation.append({"role": "assistant", "content": "Preserve this original"})
    conversation[-1]["content"] = "compacted"
    await restored.flush(conversation)
    async with db_sessionmaker() as session:
        rows = list(await session.scalars(select(CodeTranscript).where(CodeTranscript.worker_id == ids[0])))
    assert any(row.message.get("content") == "Preserve this original" for row in rows)
    assert not any(row.message.get("role") == "system" for row in rows)


async def test_mail_and_transcript_ack_are_atomic_on_failure(client, db_sessionmaker, monkeypatch):
    manager, _chat, _root, ids = await dormant_team(client, db_sessionmaker, monkeypatch)
    await manager.team.send(code_teams.Send(to="builder", message="Retain me across failure"))
    recorder = code_sessions.Transcript(manager, ids[0], code_teams.Runtime(manager, ids[0]))
    messages = await recorder.load("Start task")
    conversation = recorder.conversation(messages)
    # Force an insert failure to simulate a DB error between receiving mail and saving it.
    async with db_sessionmaker() as session:
        session.add(CodeTranscript(worker_id=ids[0], sequence=0, message={"role": "user", "content": "collision"}))
        await session.commit()
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        await recorder.flush(conversation, receive=True)
    async with db_sessionmaker() as session:
        row = await session.scalar(select(CodeTeamMessage))
        assert row.delivered is False


async def test_attached_wait_delivers_peer_question_before_worker_finishes(client, db_sessionmaker, monkeypatch):
    manager, _chat, _root, ids = await dormant_team(client, db_sessionmaker, monkeypatch)
    manager.attached.add(ids[0])
    await code_teams.Runtime(manager, ids[0]).send(code_teams.Send(to="lead", message="Which parser should I use?"))
    result = await asyncio.wait_for(manager.finish_attached(), 1)
    assert "Which parser should I use?" in result["team_mail"]
    assert ids[0] in manager.attached


async def test_mail_pagination_and_durable_order(client, db_sessionmaker, monkeypatch):
    manager, chat, _root, ids = await dormant_team(client, db_sessionmaker, monkeypatch)
    for index in range(55):
        await manager.team.send(code_teams.Send(to="builder", message=f"instruction {index}"))
    page = (await client.get(f"/api/chats/{chat['id']}/team")).json()
    assert len(page["messages"]) == 50
    assert page["messages"][0]["message"] == "instruction 5"
    older = (await client.get(f"/api/chats/{chat['id']}/team?before={page['before']}")).json()
    assert [m["message"] for m in older["messages"]] == [f"instruction {i}" for i in range(5)]
    assert older["before"] is None
    recorder = code_sessions.Transcript(manager, ids[0], code_teams.Runtime(manager, ids[0]))
    messages = await recorder.load("Keep durable mail order")
    conversation = recorder.conversation(messages)
    assert await recorder.flush(conversation, receive=True)
    assert await recorder.flush(conversation, receive=True)
    assert not await recorder.flush(conversation, receive=True)
    async with db_sessionmaker() as session:
        messages = list(await session.scalars(select(CodeTeamMessage).order_by(CodeTeamMessage.sequence)))
        assert all(row.delivered for row in messages)
