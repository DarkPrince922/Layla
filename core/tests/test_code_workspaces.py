"""Lead worktrees use real Git, keep root identity and never publish main implicitly."""
from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.code_session import CodeLeadEntry, CodeLeadTurn
from app.models.code_workspace import CodeWorkspaceCheckpoint
from app.services import agent_git, code_workers, code_workspace, files, gitops, project_agent
from tests.test_code_runner import _script, _wait
from tests.test_code_workers import setup, terminal


async def controller(client, maker):
    manager, chat, root = await setup(client, maker)

    async def credential(_url):
        return None, None

    git = agent_git.GitAgent(str(root), "Owner", "owner@example.com", credential)
    return code_workspace.Runtime(manager.runtime, str(root), git=git), manager, chat, root


async def test_worktree_keeps_dirty_files_history_index_and_context(client, db_sessionmaker):
    workspace, _manager, _chat, root = await controller(client, db_sessionmaker)
    (root / "a.txt").write_text("committed\n")
    await gitops.init(str(root), "main")
    await gitops.commit(str(root), "base", "Owner", "owner@example.com")
    head = (await gitops._git(str(root), "rev-parse", "HEAD"))[1]
    index = (root / ".git/index").read_bytes()
    (root / "a.txt").write_text("dirty\n")
    (root / "image.bin").write_bytes(b"\x00\xff")
    created = await workspace.enter(code_workspace.Enter(name="feature"))
    ident = created["workspace"]["id"]
    copy, row = await workspace.current()
    assert row.id == ident and copy != str(root)
    assert Path(copy, "a.txt").read_text() == "dirty\n"
    assert Path(copy, "image.bin").read_bytes() == b"\x00\xff"
    assert gitops.is_repo(copy) and (await gitops.status(copy))["branch"] == row.branch
    assert (root / ".git/index").read_bytes() == index
    assert (await gitops._git(str(root), "rev-parse", "HEAD"))[1] == head
    with pytest.raises(ValueError, match="уже"):
        await workspace.enter(code_workspace.Enter(name="nested"))
    kept = await workspace.exit(code_workspace.Exit())
    assert kept["workspace"]["status"] == "kept"
    assert (await workspace.current())[0] == str(root)
    fresh = code_workspace.Runtime(workspace.runtime, str(root), git=workspace.git)
    await fresh.resume(code_workspace.WorkspaceId(workspace_id=ident))
    with pytest.raises(ValueError, match="discard_changes"):
        await fresh.exit(code_workspace.Exit(action="remove"))
    await fresh.exit(code_workspace.Exit(action="remove", discard_changes=True))
    assert not Path(copy).exists() and (root / "a.txt").read_text() == "dirty\n"
    with pytest.raises(ValueError, match="нельзя"):
        await fresh.resume(code_workspace.WorkspaceId(workspace_id=ident))


async def test_code_run_switches_mid_turn_and_rollback_targets_its_workspace(client, db_sessionmaker, monkeypatch):
    _workspace, manager, chat, root = await controller(client, db_sessionmaker)
    (root / "original.txt").write_text("original\n")
    seen = []
    _script(monkeypatch, [
        [{"name": "enter_worktree", "args": {"name": "draft"}},
         {"name": "write_file", "args": {"path": "skipped.txt", "content": "never", "expected_sha256": None}}],
        [{"name": "write_file", "args": {"path": "copy.txt", "content": "in copy", "expected_sha256": None}}],
        [{"name": "git_commit", "args": {"message": "private change"}}],
        [{"name": "exit_worktree", "args": {"action": "keep"}}],
        [{"name": "read_file", "args": {"path": "original.txt"}}],
    ], seen)
    response = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "Create and verify a private draft"})
    state = await _wait(client, response.json()["id"], lambda value: value["status"] not in ("queued", "running"))
    assert state["status"] == "done", state
    rows = (await client.get(f"/api/chats/{chat['id']}/workspaces")).json()["workspaces"]
    assert len(rows) == 1 and rows[0]["status"] == "kept"
    ident, copy = rows[0]["id"], Path(rows[0]["path"])
    assert (copy / "copy.txt").read_text() == "in copy"
    assert not (root / "copy.txt").exists() and not (root / "skipped.txt").exists() and not (copy / "skipped.txt").exists()
    assert any("Workspace changed; this call was not executed" in str(item["conversation"]) for item in seen)
    assert any(str(copy) in item["conversation"][0]["content"] for item in seen)
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    message_id = detail["messages"][-1]["id"]
    async with db_sessionmaker() as session:
        assert (await session.get(CodeLeadTurn, message_id)).status == "done"
        mapping = await session.scalar(select(CodeWorkspaceCheckpoint))
        assert mapping.workspace_id == ident
    diff = (await client.get(f"/api/chats/{chat['id']}/workspaces/{ident}/diff")).json()
    assert "+in copy" in diff["diff"]
    archive = await client.get(f"/api/chats/{chat['id']}/workspaces/{ident}/archive")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        assert zipped.read("copy.txt") == b"in copy"
        assert not any(".git" in name.split("/") for name in zipped.namelist())
    rollback = await client.post(f"/api/chats/{chat['id']}/messages/{message_id}/rollback")
    assert rollback.status_code == 200, rollback.text
    assert not (copy / "copy.txt").exists() and (root / "original.txt").read_text() == "original\n"
    async with db_sessionmaker() as session:
        assert not (await session.get(CodeLeadTurn, message_id)).active
        assert list(await session.scalars(select(CodeLeadEntry).where(CodeLeadEntry.turn_id == message_id)))
    # Scoping applies to diff, archive, file reads and transitions, not just UI state.
    other = (await client.post("/api/chats", json={"domain": "code", "project_id": manager.project_id, "model": "m"})).json()
    for route in ("diff", "archive", "files"):
        assert (await client.get(f"/api/chats/{other['id']}/workspaces/{ident}/{route}")).status_code == 404
    assert (await client.post(f"/api/chats/{other['id']}/workspaces/resume", json={"workspace_id": ident})).status_code == 409


async def test_workspace_git_publication_uses_own_branch_and_keeps_main(client, db_sessionmaker, monkeypatch, tmp_path):
    workspace, _manager, _chat, root = await controller(client, db_sessionmaker)
    (root / "a.txt").write_text("base")
    await gitops.init(str(root), "main")
    await gitops.commit(str(root), "base", "Owner", "owner@example.com")
    bare = tmp_path / "remote.git"
    bare.mkdir()
    raw = gitops._git
    await raw(str(bare), "init", "--bare", "-b", "main")
    await gitops.set_remote(str(root), "https://example.test/owner/repo.git")

    async def local_network(path, *args, config=None, **kwargs):
        # Only this fixture enables local transport; production accepts HTTPS.
        extra = {"protocol.file.allow": "always", f"url.{bare}.insteadOf": "https://example.test/owner/repo.git"} if args[0] in ("fetch", "push", "ls-remote") else {}
        return await raw(path, *args, config={**(config or {}), **extra}, **kwargs)

    monkeypatch.setattr(gitops, "_git", local_network)
    await gitops.push(str(root), None, branch="main")
    original = (await raw(str(bare), "rev-parse", "refs/heads/main"))[1]
    created = await workspace.enter(code_workspace.Enter(name="publish"))
    copy = created["workspace"]["path"]
    branch = created["workspace"]["branch"]
    Path(copy, "a.txt").write_text("changed")
    restricted = agent_git.GitAgent(copy, workspace.git.author, workspace.git.email,
                                   workspace.git.credential, publish_branch=branch)
    assert "error" in await restricted.call("git_push", {"branch": "main"})
    assert "error" in await restricted.call("git_sync", {"branch": "main"})
    assert "error" in await workspace.publish(code_workspace.WorkspaceId(workspace_id=created["workspace"]["id"]))
    assert "error" not in await restricted.call("git_commit", {"message": "draft"})
    with pytest.raises(ValueError, match="discard_changes"):
        await workspace.exit(code_workspace.Exit(action="remove"))
    await workspace.exit(code_workspace.Exit())
    result = await workspace.publish(code_workspace.WorkspaceId(workspace_id=created["workspace"]["id"]))
    assert "error" not in result, result
    assert (await raw(str(bare), "rev-parse", "refs/heads/main"))[1] == original
    assert (await raw(str(bare), "rev-parse", "refs/heads/" + branch))[1] != original
    assert (root / "a.txt").read_text() == "base"
    assert (await gitops.status(str(root)))["branch"] == "main"


async def test_workspace_worker_scope_and_transition_guard(client, db_sessionmaker, monkeypatch):
    workspace, manager, chat, root = await controller(client, db_sessionmaker)
    created = await workspace.enter(code_workspace.Enter(name="workers"))
    ident, copy = created["workspace"]["id"], created["workspace"]["path"]
    bound = code_workers.Manager(manager.runtime, copy, provider_id=manager.provider_id, model="m",
                                 source_root=str(root), workspace_id=ident)
    parked = asyncio.Event()

    async def child(_provider, _key, _model, _messages, child_root, _permissions, **_):
        assert child_root != copy
        files.change_file(child_root, "child.txt", "private child result", None)
        parked.set()
        await asyncio.Event().wait()
        yield {"delta": "unreachable"}

    monkeypatch.setattr(project_agent, "run", child)
    worker_id = (await bound.start_agent(code_workers.AgentStart(task="Implement inside current workspace", detached=True)))["worker"]["id"]
    await asyncio.wait_for(parked.wait(), 5)
    with pytest.raises(ValueError, match="воркеров"):
        await workspace.exit(code_workspace.Exit())
    await bound.stop(worker_id)
    assert (await terminal(bound, worker_id))["worker"]["changes"]
    await workspace.exit(code_workspace.Exit())
    assert not await manager.list()
    with pytest.raises(ValueError, match="Переключитесь"):
        await manager.output(code_workers.WorkerOutput(worker_id=worker_id))
    await workspace.resume(code_workspace.WorkspaceId(workspace_id=ident))
    events = [item async for item in project_agent._apply_worker(bound, {"worker_id": worker_id},
        {"id": "apply", "name": "worker_apply"}, None, copy, set())]
    assert any("checkpoint" in item for item in events)
    assert Path(copy, "child.txt").exists() and not (root / "child.txt").exists()
    # Trash cleanup includes lead and nested child storage, never source files.
    response = await client.delete(f"/api/chats/{chat['id']}")
    assert response.status_code == 204, response.text
    assert Path(copy).exists()  # soft deletion remains restorable
    assert (await client.delete(f"/api/trash/chats/{chat['id']}")).status_code == 204
    assert not Path(copy).exists() and root.exists()


async def test_arbitrary_git_markers_links_and_read_only_modes_are_rejected(client, db_sessionmaker, tmp_path):
    workspace, _manager, _chat, root = await controller(client, db_sessionmaker)
    external = tmp_path / "outside"
    external.mkdir()
    await gitops.init(str(external))
    (root / ".git").write_text("gitdir: " + str(external / ".git"))
    assert not gitops.is_repo(str(root))
    for mode in ("plan", "review"):
        restricted = code_workspace.Runtime(workspace.runtime, str(root), mode=mode)
        assert {tool["function"]["name"] for tool in restricted.tools()} == {"worktree_list", "worktree_diff", "worktree_read"}
        assert "error" in await restricted.call("enter_worktree", {})
    (root / ".git").unlink()
    created = await workspace.enter(code_workspace.Enter(name="links"))
    copy = Path(created["workspace"]["path"])
    (copy / "secret").symlink_to(external)
    assert "error" in await workspace.call("worktree_read", {"workspace_id": created["workspace"]["id"], "path": "secret/config"})
    (copy / ".git").write_text("gitdir: " + str(external / ".git"))
    assert not gitops.is_repo(str(copy))
    with pytest.raises(ValueError, match="не подставляется"):
        await workspace.current()


async def test_removed_workspace_checkpoint_never_restores_into_source(client, db_sessionmaker, monkeypatch):
    workspace, _manager, chat, root = await controller(client, db_sessionmaker)
    (root / "same.txt").write_text("source stays here")
    sha = files.read_file(root, "same.txt")["sha256"]
    _script(monkeypatch, [[{"name": "enter_worktree", "args": {"name": "remove"}}],
        [{"name": "write_file", "args": {"path": "same.txt", "content": "copy change", "expected_sha256": sha}}]])
    response = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "Edit a private copy"})
    state = await _wait(client, response.json()["id"], lambda row: row["status"] not in ("queued", "running"))
    assert state["status"] == "done", state
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    await workspace.exit(code_workspace.Exit(action="remove", discard_changes=True))
    rollback = await client.post(f"/api/chats/{chat['id']}/messages/{detail['messages'][-1]['id']}/rollback")
    assert rollback.status_code == 409, rollback.text
    assert (root / "same.txt").read_text() == "source stays here"
