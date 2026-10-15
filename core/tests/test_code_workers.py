"""Real Git worktrees, independent jobs, import conflicts and identity boundaries."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.code_worker import CodeWorker
from app.models.job import Job
from app.models.user import Project
from app.services import (
    code_runner,
    code_state,
    code_workers,
    code_worktrees,
    files,
    project_agent,
    sandbox,
)


async def setup(client, maker):
    response = await client.post("/api/auth/register", json={"email": "workers@example.com", "password": "hunter2hunter2"})
    owner = response.json()["id"]
    project = (await client.post("/api/projects", json={"name": "Workers"})).json()
    provider = (await client.post("/api/providers", json={"name": "Mock", "kind": "openai_compatible",
                 "base_url": "http://mock/v1", "default_model": "m", "active": True})).json()
    chat = (await client.post("/api/chats", json={"domain": "code", "project_id": project["id"], "model": "m"})).json()
    async with maker() as session:
        row = await session.get(Project, project["id"])
        root = row.path
    runtime = code_state.Runtime(maker, owner, chat["id"], project["id"])
    manager = code_workers.Manager(runtime, root, provider_id=provider["id"], model="m")
    return manager, chat, Path(root)


async def terminal(manager, worker_id):
    for _ in range(1000):
        result = await manager.wait(code_workers.WorkerWait(worker_id=worker_id, timeout=0))
        if result["worker"]["status"] not in code_workers.ACTIVE:
            return result
        await asyncio.sleep(0.01)
    raise AssertionError("worker did not finish")


async def test_worktree_preserves_dirty_ignored_and_binary_inputs_without_changing_source(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".gitignore").write_text("ignored.txt\n")
    (root / ".gitattributes").write_text("*.txt text eol=lf\n")
    (root / "ignored.txt").write_bytes(b"uncommitted\r\n")
    (root / "image.bin").write_bytes(b"\x00\xff\x01")
    (root / "build").mkdir()
    (root / "build/keep.txt").write_text("keep\n")
    project_id, worker_id = str(uuid4()), str(uuid4())
    state = await code_worktrees.create(str(root), project_id, worker_id)
    location, repository, worktree = code_worktrees.paths(str(root), project_id, worker_id)
    assert (worktree / ".git").is_file()
    assert (worktree / "ignored.txt").read_bytes() == b"uncommitted\r\n"
    assert (worktree / "image.bin").read_bytes() == b"\x00\xff\x01"
    assert not (root / ".git").exists()
    assert code_worktrees.diff(str(root), project_id, worker_id, state["baseline"]) == []
    files.change_file(worktree, "new.py", "print(42)\n", None)
    changes = code_worktrees.diff(str(root), project_id, worker_id, state["baseline"])
    assert [change["path"] for change in changes] == ["new.py"]
    assert not (root / "new.py").exists()
    assert (repository / "ignored.txt").read_bytes() == b"uncommitted\r\n"
    await code_worktrees.remove(str(root), project_id, worker_id)
    assert not location.exists() and (root / "ignored.txt").exists()


async def test_implementing_agent_is_isolated_and_import_has_checkpoint(client, db_sessionmaker, monkeypatch):
    manager, _chat, root = await setup(client, db_sessionmaker)
    (root / "app.py").write_text("old\n")

    async def child(_provider, _key, _model, _messages, worktree, permissions, **kwargs):
        assert worktree != str(root) and kwargs["worker_context"] is True
        assert kwargs.get("workers") is None and kwargs.get("git") is None
        current = files.read_file(worktree, "app.py")
        files.change_file(worktree, "app.py", "new\n", current["sha256"])
        files.change_file(worktree, "added.txt", "created\n", None)
        yield {"delta": "Implemented and verified"}

    monkeypatch.setattr(project_agent, "run", child)
    started = await manager.start_agent(code_workers.AgentStart(task="Implement the requested feature"))
    ident = started["worker"]["id"]
    result = await terminal(manager, ident)
    assert result["worker"]["status"] == "done", result
    assert (root / "app.py").read_text() == "old\n" and not (root / "added.txt").exists()
    assert {change["path"] for change in result["worker"]["changes"]} == {"app.py", "added.txt"}
    events = [event async for event in project_agent._apply_worker(
        manager, {"worker_id": ident}, {"id": "apply", "name": "worker_apply", "path": ""}, None, str(root), set(),
    )]
    assert (root / "app.py").read_text() == "new\n" and (root / "added.txt").exists()
    assert {event["checkpoint"]["path"] for event in events if "checkpoint" in event} == {"app.py", "added.txt"}
    assert next(event["checkpoint"]["content"] for event in events if event.get("checkpoint", {}).get("path") == "app.py") == "old\n"
    assert len((await manager.output(code_workers.WorkerOutput(worker_id=ident)))["worker"]["applied"]) == 2


async def test_import_conflicts_preserve_main_files_and_private_results(client, db_sessionmaker, monkeypatch):
    manager, _chat, root = await setup(client, db_sessionmaker)
    (root / "app.py").write_text("base")

    async def child(_provider, _key, _model, _messages, worktree, _permissions, **_kwargs):
        read = files.read_file(worktree, "app.py")
        files.change_file(worktree, "app.py", "worker", read["sha256"])
        yield {"delta": "Done"}

    monkeypatch.setattr(project_agent, "run", child)
    ident = (await manager.start_agent(code_workers.AgentStart(task="Change app implementation")))["worker"]["id"]
    await terminal(manager, ident)
    current = files.read_file(root, "app.py")
    files.change_file(root, "app.py", "editor", current["sha256"])
    with pytest.raises(files.FileConflict):
        await manager.proposals(code_workers.WorkerApply(worker_id=ident))
    assert (root / "app.py").read_text() == "editor"
    assert code_worktrees.paths(str(root), manager.project_id, ident)[2].joinpath("app.py").read_text() == "worker"


async def test_three_slots_are_atomic_and_worker_stop_resume_keeps_changes(client, db_sessionmaker, monkeypatch):
    manager, _chat, root = await setup(client, db_sessionmaker)
    entered = asyncio.Event()
    entered_roots = set()

    async def child(_provider, _key, _model, _messages, worktree, _permissions, **_kwargs):
        if not (Path(worktree) / "saved.py").exists():
            files.change_file(worktree, "saved.py", "saved before stopping", None)
        entered_roots.add(worktree)
        if len(entered_roots) == 3:
            entered.set()
        yield {"delta": "Working"}
        await asyncio.Event().wait()

    monkeypatch.setattr(project_agent, "run", child)
    results = await asyncio.gather(*[
        manager.call("agent_start", {"task": f"Investigate independent task {index}"}) for index in range(4)
    ])
    assert len([result for result in results if "worker" in result]) == 3, results
    assert len([result for result in results if "error" in result]) == 1
    await asyncio.wait_for(entered.wait(), 10)
    ident = next(result["worker"]["id"] for result in results if "worker" in result)
    stopped = await manager.stop(ident)
    assert stopped["worker"]["status"] == "cancelled"
    assert code_worktrees.paths(str(root), manager.project_id, ident)[2].joinpath("saved.py").exists()
    resumed = await manager.resume(ident)
    assert resumed["worker"]["id"] == ident
    await manager.cancel_attached()


async def test_worker_output_and_controls_do_not_cross_chat_or_account(client, db_sessionmaker, monkeypatch):
    manager, chat, _root = await setup(client, db_sessionmaker)

    async def child(*_args, **_kwargs):
        yield {"delta": "Private report"}

    monkeypatch.setattr(project_agent, "run", child)
    ident = (await manager.start_agent(code_workers.AgentStart(task="Study project entry points")))["worker"]["id"]
    await terminal(manager, ident)
    other = (await client.post("/api/chats", json={"domain": "code", "project_id": manager.project_id, "model": "m"})).json()
    assert (await client.get(f"/api/chats/{other['id']}/workers/{ident}")).status_code == 404
    await client.post("/api/auth/register", json={"email": "another-worker@example.com", "password": "hunter2hunter2"})
    assert (await client.get(f"/api/chats/{chat['id']}/workers/{ident}")).status_code == 404
    assert (await client.post(f"/api/chats/{chat['id']}/workers/{ident}/stop")).status_code == 404


async def test_background_command_output_wait_and_cancellation(client, db_sessionmaker, monkeypatch):
    manager, _chat, root = await setup(client, db_sessionmaker)
    cancel = asyncio.Event()

    async def info(**_kwargs):
        return {"tools": {"bash": "5"}}

    async def command(runner, args):
        assert runner.project_id != manager.project_id and runner.root != str(root)
        yield {"output": "step one\n"}
        try:
            await asyncio.Event().wait()
        finally:
            cancel.set()

    monkeypatch.setattr(sandbox, "info", info)
    monkeypatch.setattr(code_runner.Runner, "command", command)
    ident = (await manager.start_command(code_workers.CommandStart(command="pytest -q")))["worker"]["id"]
    for _ in range(200):
        result = await manager.output(code_workers.WorkerOutput(worker_id=ident))
        if "step one" in result["output"]:
            break
        await asyncio.sleep(0.01)
    assert "step one" in result["output"]
    result = await manager.wait(code_workers.WorkerWait(worker_id=ident, timeout=0))
    assert result["worker"]["status"] == "running"
    await manager.stop(ident)
    assert cancel.is_set()


async def test_restart_marks_worker_interrupted_and_preserves_workspace(client, db_sessionmaker):
    manager, _chat, root = await setup(client, db_sessionmaker)
    ident = str(uuid4())
    state = await code_worktrees.create(str(root), manager.project_id, ident)
    async with db_sessionmaker() as session:
        job = Job(owner_id=manager.owner_id, domain="code", status="error", kind="code.worker")
        session.add(job)
        await session.flush()
        session.add(CodeWorker(id=ident, owner_id=manager.owner_id, chat_id=manager.chat_id,
                               project_id=manager.project_id, job_id=job.id, slot=0, kind="agent",
                               task="Implement something", role="implement", status="running",
                               payload={**state, "provider_id": manager.provider_id, "model": "m"},
                               output="saved output"))
        await session.commit()
    result = (await manager.list())[0]
    assert result["status"] == "interrupted"
    assert code_worktrees.paths(str(root), manager.project_id, ident)[2].is_dir()
    assert (await manager.output(code_workers.WorkerOutput(worker_id=ident)))["output"] == "saved output"


async def test_failed_command_persists_output_and_failed_job(client, db_sessionmaker, monkeypatch):
    manager, _chat, _root = await setup(client, db_sessionmaker)

    async def info(**_kwargs):
        return {"tools": {"bash": "5"}}

    async def command(_runner, _args):
        yield {"output": "Assertion failed\n"}
        yield {"result": {"exit_code": 1}}

    monkeypatch.setattr(sandbox, "info", info)
    monkeypatch.setattr(code_runner.Runner, "command", command)
    started = await manager.start_command(code_workers.CommandStart(command="pytest -q"))
    ident = started["worker"]["id"]
    result = await terminal(manager, ident)
    assert result["worker"]["status"] == "error" and "Assertion failed" in result["output"]
    # Worker finalization precedes the enclosing job finalization by one commit.
    from app.services import jobs

    for _ in range(200):
        if not jobs.has_worker(started["worker"]["job_id"]):
            break
        await asyncio.sleep(0.01)
    async with db_sessionmaker() as session:
        assert (await session.get(Job, started["worker"]["job_id"])).status == "error"


async def test_readonly_modes_and_personas_cannot_start_implementation_or_import(client, db_sessionmaker):
    manager, _chat, _root = await setup(client, db_sessionmaker)
    manager.mode = "plan"
    result = await manager.call("agent_start", {"task": "Change source code now", "role": "implement"})
    assert "error" in result
    assert "worker_apply" not in {tool["function"]["name"] for tool in manager.tools()}
    manager.mode, manager.permissions = "auto", ["files.read"]
    assert "error" in await manager.call("agent_start", {"task": "Change source code now"})
    assert "error" in await manager.call("command_start", {"command": "true"})


async def test_lead_collects_worker_and_imports_without_continue_message(client, db_sessionmaker, monkeypatch):
    manager, chat, root = await setup(client, db_sessionmaker)
    lead_turns, child_turns = 0, 0

    def call(name, args):
        return {"id": "tool", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

    async def stream(_provider, _key, _model, conversation, available, **_kwargs):
        nonlocal lead_turns, child_turns
        names = {tool["function"]["name"] for tool in available}
        if "agent_start" in names:
            lead_turns += 1
            if lead_turns == 1:
                yield "tool_calls", [call("agent_start", {"task": "Create result.py with an answer"})]
            elif lead_turns == 2:
                yield "content", "Worker launched. "
            elif lead_turns == 3:
                completed = json.loads(conversation[-1]["content"].split("\n", 1)[1])
                ident = completed["workers_completed"][0]["worker"]["id"]
                yield "tool_calls", [call("worker_apply", {"worker_id": ident})]
            else:
                yield "content", "All done."
        else:
            assert "delegate_agent" not in names and "worker_apply" not in names
            child_turns += 1
            if child_turns == 1:
                yield "tool_calls", [call("write_file", {"path": "result.py", "content": "answer = 42\n"})]
            else:
                yield "content", "Created result.py."

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    started = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "Implement with one independent worker"})
    assert started.status_code == 202, started.text
    for _ in range(1000):
        job = (await client.get(f"/api/jobs/{started.json()['id']}")).json()
        if job["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.01)
    assert job["status"] == "done", job
    assert (root / "result.py").exists(), {"job": job, "workers": await manager.list(), "turns": (lead_turns, child_turns)}
    assert (root / "result.py").read_text() == "answer = 42\n"
    assert lead_turns == 4 and child_turns == 2
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    assert detail["messages"][-1]["meta"]["checkpoint"] is True
    assert len((await client.get("/api/chats?domain=code")).json()) == 1
    assert (await manager.list())[0]["applied"]["result.py"]


@pytest.mark.parametrize("detached", [False, True])
async def test_parent_cancellation_stops_only_attached_workers(client, db_sessionmaker, monkeypatch, detached):
    manager, _chat, _root = await setup(client, db_sessionmaker)
    started, stopped = asyncio.Event(), asyncio.Event()
    streams = []

    async def child_stream():
        try:
            yield {"delta": "Working"}
            await asyncio.Event().wait()
        finally:
            stopped.set()

    def child(*_args, **_kwargs):
        stream = child_stream()
        streams.append(stream)  # Cleanup must be explicit, independent of garbage collection.
        return stream

    save = manager._save

    async def saving_output(worker_id, **values):
        if values.get("output") == "Working" and "status" not in values:
            # Cancel while the consumer saves an event, with the generator paused at yield.
            started.set()
            await asyncio.Event().wait()
        await save(worker_id, **values)

    monkeypatch.setattr(manager, "_save", saving_output)
    monkeypatch.setattr(project_agent, "run", child)
    ident = (await manager.start_agent(code_workers.AgentStart(task="Explore module behavior", detached=detached)))["worker"]["id"]
    await asyncio.wait_for(started.wait(), 10)

    async def parent():
        async with project_agent._worker_lifetime(manager):
            await manager.finish_attached()
            await asyncio.Event().wait()

    task = asyncio.create_task(parent())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set() is (not detached)
    if detached:
        assert (await manager.output(code_workers.WorkerOutput(worker_id=ident)))["worker"]["status"] == "running"
        await manager.stop(ident)
        assert stopped.is_set()


async def test_confirm_import_rejection_and_project_deletion_lifecycle(client, db_sessionmaker, monkeypatch):
    manager, chat, root = await setup(client, db_sessionmaker)
    child_ready = asyncio.Event()

    async def child(_provider, _key, _model, _messages, worktree, _permissions, **_kwargs):
        files.change_file(worktree, "new.txt", "from worker", None)
        child_ready.set()
        yield {"delta": "Working"}
        await asyncio.Event().wait()

    monkeypatch.setattr(project_agent, "run", child)
    ident = (await manager.start_agent(code_workers.AgentStart(task="Implement the new file")))["worker"]["id"]
    await asyncio.wait_for(child_ready.wait(), 10)
    assert (await client.delete(f"/api/chats/{chat['id']}")).status_code == 409
    assert (await client.delete(f"/api/projects/{manager.project_id}")).status_code == 409
    cleared = (await client.delete("/api/chats?domain=code")).json()
    assert cleared["skipped"] == 1 and cleared["deleted"] == 0
    await manager.stop(ident)

    async def reject(event):
        assert "+from worker" in event["change"]["diff"]
        return "reject"

    events = [event async for event in project_agent._apply_worker(
        manager, {"worker_id": ident}, {"id": "apply", "name": "worker_apply", "path": ""}, reject, str(root), set(),
    )]
    assert "error" in events[-1]["outcome"] and not (root / "new.txt").exists()
    assert not any("checkpoint" in event for event in events)
    location = code_worktrees.paths(str(root), manager.project_id, ident)[0]
    assert location.is_dir()
    assert (await client.delete(f"/api/chats/{chat['id']}")).status_code == 204
    assert (await client.delete(f"/api/trash/chats/{chat['id']}")).status_code == 204
    assert not location.exists() and root.exists()


async def test_resumed_worker_can_revert_a_previously_imported_edit(client, db_sessionmaker, monkeypatch):
    manager, _chat, root = await setup(client, db_sessionmaker)
    (root / "app.txt").write_text("original")
    attempts = 0

    async def child(_provider, _key, _model, _messages, worktree, _permissions, **_kwargs):
        nonlocal attempts
        attempts += 1
        current = files.read_file(worktree, "app.txt")
        files.change_file(worktree, "app.txt", "updated" if attempts == 1 else "original", current["sha256"])
        yield {"delta": "Done"}

    monkeypatch.setattr(project_agent, "run", child)
    ident = (await manager.start_agent(code_workers.AgentStart(task="Update application behavior")))["worker"]["id"]
    await terminal(manager, ident)
    _ = [event async for event in project_agent._apply_worker(
        manager, {"worker_id": ident}, {"id": "first", "name": "worker_apply", "path": ""}, None, str(root), set(),
    )]
    assert (root / "app.txt").read_text() == "updated"
    async with db_sessionmaker() as session:
        row = await session.get(CodeWorker, ident)
        row.status = "interrupted"
        await session.commit()
    await manager.resume(ident)
    result = await terminal(manager, ident)
    assert result["worker"]["changes"][0]["before_sha256"] == files.read_file(root, "app.txt")["sha256"]
    # An older import does not authorize silently discarding new resumed edits.
    with pytest.raises(ValueError, match="неприменённые"):
        await manager.discard(code_workers.WorkerDiscard(worker_id=ident))
    assert "diff" not in (await manager.list())[0]["changes"][0]
    _ = [event async for event in project_agent._apply_worker(
        manager, {"worker_id": ident}, {"id": "second", "name": "worker_apply", "path": ""}, None, str(root), set(),
    )]
    assert (root / "app.txt").read_text() == "original"
