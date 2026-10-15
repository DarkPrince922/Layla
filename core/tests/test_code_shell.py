"""Real benign commands over the sandbox protocol, plus import/ownership/rollback contracts."""
from __future__ import annotations

import asyncio
import base64
import json
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.services import (
    code_runner,
    code_shell,
    code_workers,
    code_workspace,
    files,
    project_agent,
    sandbox,
)
from tests.test_code_runner import _script, _wait
from tests.test_code_workers import setup, terminal
from tests.test_code_workspaces import controller


class StreamingASGI(httpx.AsyncBaseTransport):
    """ASGITransport buffers responses; this adapter preserves real streaming/cancellation semantics."""
    def __init__(self, app):
        self.app = app

    async def handle_async_request(self, request):
        loop = asyncio.get_running_loop()
        started, chunks, disconnected = loop.create_future(), asyncio.Queue(), asyncio.Event()
        sent = False
        body = await request.aread()

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                started.set_result(message)
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    await chunks.put(message["body"])
                if not message.get("more_body", False):
                    await chunks.put(None)

        async def run_app():
            try:
                await self.app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                                "method": request.method, "scheme": "http", "path": request.url.path,
                                "raw_path": request.url.raw_path.split(b"?")[0], "query_string": request.url.query,
                                "headers": [(key.lower(), value) for key, value in request.headers.raw], "root_path": "", "server": ("sandbox", 80),
                                "client": ("127.0.0.1", 1)}, receive, send)
            except Exception as exc:  # noqa: BLE001 — transport relays application exceptions to the client
                if not started.done():
                    started.set_exception(exc)
                await chunks.put(exc)

        task = asyncio.create_task(run_app())

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                while True:
                    chunk = await chunks.get()
                    if chunk is None:
                        return
                    if isinstance(chunk, Exception):
                        raise chunk
                    yield chunk

            async def aclose(self):
                disconnected.set()
                await asyncio.wait_for(asyncio.shield(task), 10)

        response = await started
        return httpx.Response(response["status"], headers=response.get("headers", []), stream=Body())


def item(path, before, after, *, mode=0o644, before_mode=0o644):
    return {"path": path, "before_sha256": files._sha(before) if before is not None else None,
            "after_sha256": files._sha(after) if after is not None else None,
            "before_mode": before_mode if before is not None else None,
            "mode": mode if after is not None else None,
            "data": base64.b64encode(after).decode() if after is not None else None}


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    # Exercise the real protocol/helpers/commands locally; uid isolation has separate sandbox CI tests.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "sandbox"))
    from layla_sandbox import config, server
    from layla_sandbox import workspace as workspace_module
    from layla_sandbox.workspace import Users, Workspace, prepare_root
    work = tmp_path / "sandbox"
    prepare_root(work)
    monkeypatch.setattr(config, "WORK", work)
    monkeypatch.setattr(config, "PROXY", "")
    monkeypatch.setattr(server, "users", Users(work))
    monkeypatch.setattr(server, "_locks", defaultdict(asyncio.Lock))
    monkeypatch.setattr(server, "_slots", None)
    monkeypatch.setattr(server, "processes", {})
    monkeypatch.setattr(Workspace, "_as_user", lambda self: {})
    monkeypatch.setattr(Workspace, "ensure_user_dir", lambda self: self.user_dir.mkdir(mode=0o700, parents=True, exist_ok=True))
    if not Path("/proc").is_dir():
        monkeypatch.setattr(workspace_module, "session_pids", lambda _sid: [])

    async def venv(self):
        (self.path / ".venv").mkdir(exist_ok=True)
        return True

    async def info(**_kwargs):
        return {"file_changes": 1, "interactive_stdin": True, "pty": True, "shells": ["bash"], "tools": {}}

    async def runtimes(**_kwargs):
        return []

    monkeypatch.setattr(Workspace, "ensure_venv", venv)
    monkeypatch.setattr(sandbox, "info", info)
    monkeypatch.setattr(sandbox, "runtimes", runtimes)
    monkeypatch.setattr(sandbox, "_client", lambda timeout: httpx.AsyncClient(
        transport=StreamingASGI(server.app), base_url="http://sandbox", timeout=timeout))
    return server


@pytest.mark.asyncio
async def test_import_binary_modes_deletions_and_rollback(tmp_path, monkeypatch):
    root = str(tmp_path)
    (tmp_path / "old.txt").write_bytes(b"old\r\n")
    (tmp_path / "gone.txt").write_text("remove")
    (tmp_path / "image.bin").write_bytes(b"\0old")
    packet = {"id": str(uuid4()), "changes": [item("old.txt", b"old\r\n", b"new\r\n", mode=0o755),
              item("gone.txt", b"remove", None), item("image.bin", b"\0old", b"\xff\0new"),
              item("new.txt", None, b"created")], "blocked": {}}
    acknowledged = []

    async def request(*args):
        acknowledged.append(args)
        return {}

    monkeypatch.setattr(sandbox, "shell_request", request)
    runner = code_runner.Runner("owner", "project", root)
    events = [event async for event in code_shell.apply(runner, packet, {"id": "run", "name": "run_command"})]
    assert len(acknowledged) == 1
    assert (tmp_path / "image.bin").read_bytes() == b"\xff\0new"
    assert not (tmp_path / "gone.txt").exists() and (tmp_path / "old.txt").stat().st_mode & 0o111
    assert len([event for event in events if "tool" in event]) == 4
    for point in reversed([event["checkpoint"] for event in events if "checkpoint" in event]):
        code_shell.restore(root, point)
    assert (tmp_path / "image.bin").read_bytes() == b"\0old"
    assert (tmp_path / "old.txt").read_bytes() == b"old\r\n"
    assert not (tmp_path / "old.txt").stat().st_mode & 0o111
    assert (tmp_path / "gone.txt").read_text() == "remove" and not (tmp_path / "new.txt").exists()


@pytest.mark.asyncio
async def test_conflicts_permissions_rejection_and_retry_keep_packet(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("manual")
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    packet = {"id": str(uuid4()), "changes": [item("a.txt", b"old", b"generated"), item("new", None, b"new")]}

    async def no_ack(*_args):
        raise AssertionError("must retain result")

    monkeypatch.setattr(sandbox, "shell_request", no_ack)
    result = [event async for event in code_shell.apply(runner, packet, {"id": "run"})]
    assert result[-1]["shell_result"]["pending_changes"] == packet["id"]
    assert (tmp_path / "a.txt").read_text() == "manual" and not (tmp_path / "new").exists()
    packet["changes"] = [item("new", None, b"new")]
    readonly = [event async for event in code_shell.apply(runner, packet, {"id": "run"}, writable=False)]
    assert "персона" in readonly[-1]["shell_result"]["error"] and not (tmp_path / "new").exists()

    async def reject(_event):
        return "reject"

    rejected = [event async for event in code_shell.apply(runner, packet, {"id": "run"}, ask=reject)]
    assert any(event.get("tool", {}).get("status") == "pending" for event in rejected)
    assert not (tmp_path / "new").exists() and "отклонён" in rejected[-1]["shell_result"]["error"]


@pytest.mark.parametrize("path", ["../escape", ".git/config", "/absolute", "x\\y"])
def test_export_packet_cannot_escape_project(tmp_path, path):
    with pytest.raises(ValueError):
        code_shell.prepare(str(tmp_path), {"changes": [item(path, None, b"bad")]})


@pytest.mark.asyncio
async def test_terminal_protocol_roundtrip_failed_command_and_durable_undo(client, bridge):
    await client.post("/api/auth/register", json={"email": "shell@example.com", "password": "hunter2hunter2"})
    project = (await client.post("/api/projects", json={"name": "Shell"})).json()
    root = Path(project["path"])
    (root / "old").write_text("old")
    (root / "old").chmod(0o600)
    command = "printf generated > old; printf 'image' > asset.bin; chmod +x old; exit 7"
    response = await client.post(f"/api/projects/{project['id']}/run", json={"command": command})
    events = [json.loads(frame.removeprefix("data: ")) for frame in response.text.strip().split("\n\n")]
    assert next(event for event in events if event["type"] == "exit")["code"] == 7
    assert (root / "old").read_text() == "generated" and (root / "asset.bin").exists()
    assert len([event for event in events if event["type"] == "file_change"]) == 2
    pending = (await client.get(f"/api/projects/{project['id']}/command-changes")).json()
    assert pending["id"] is None
    history = (await client.get(f"/api/projects/{project['id']}/shell-history")).json()
    assert len(history) == 1 and not history[0]["rolled_back"]
    run_id = history[0]["id"]
    (root / "old").write_text("later editor")
    assert (await client.post(f"/api/projects/{project['id']}/shell-history/{run_id}/rollback")).status_code == 409
    assert (root / "asset.bin").exists()  # preflight rejected the entire undo
    (root / "old").write_text("generated")
    restored = await client.post(f"/api/projects/{project['id']}/shell-history/{run_id}/rollback")
    assert restored.status_code == 200, restored.text
    assert (root / "old").read_text() == "old" and not (root / "asset.bin").exists()
    assert not (root / "old").stat().st_mode & 0o111
    assert (root / "old").stat().st_mode & 0o777 == 0o600
    await client.post("/api/auth/logout")
    await client.post("/api/auth/register", json={"email": "other-shell@example.com", "password": "hunter2hunter2"})
    assert (await client.get(f"/api/projects/{project['id']}/shell-history")).status_code == 404
    assert (await client.post(f"/api/projects/{project['id']}/command-changes", json={"action": "discard", "change_id": str(uuid4())})).status_code == 404


@pytest.mark.asyncio
async def test_chat_command_saves_actual_diffs_and_binary_checkpoint(client, db_sessionmaker, bridge, monkeypatch):
    _manager, chat, root = await setup(client, db_sessionmaker)
    (root / "old.bin").write_bytes(b"\0before")
    _script(monkeypatch, [[{"name": "run_command", "args": {"command": "printf '\\000after' > old.bin; printf done > generated.txt"}}]])
    response = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "Generate files", "mode": "auto"})
    done = await _wait(client, response.json()["id"], lambda job: job["status"] in ("done", "error"))
    assert done["status"] == "done", done
    assert (root / "old.bin").read_bytes() == b"\0after" and (root / "generated.txt").exists()
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    message = detail["messages"][-1]
    assert message["meta"]["checkpoint"]
    assert {tool["path"] for tool in message["meta"]["tools"] if tool.get("change")} == {"old.bin", "generated.txt"}
    rollback = await client.post(f"/api/chats/{chat['id']}/messages/{message['id']}/rollback")
    assert rollback.status_code == 200, rollback.text
    assert (root / "old.bin").read_bytes() == b"\0before" and not (root / "generated.txt").exists()


@pytest.mark.asyncio
async def test_native_conflict_retains_packet_and_rejects_next_sync(client, db_sessionmaker, bridge):
    manager, _chat, root = await setup(client, db_sessionmaker)
    (root / "same.txt").write_text("original")
    task = asyncio.create_task(client.post(f"/api/projects/{manager.project_id}/run", json={"command": "printf generated > same.txt; sleep 0.3"}))
    for _ in range(500):
        if (manager.owner_id, manager.project_id) in bridge.processes:
            break
        await asyncio.sleep(0.01)
    files.change_file(root, "same.txt", "editor", files.read_file(root, "same.txt")["sha256"])
    response = await task
    assert "pending_changes" in response.text and (root / "same.txt").read_text() == "editor"
    packet = (await client.get(f"/api/projects/{manager.project_id}/command-changes")).json()
    assert packet["id"] and packet["changes"][0]["path"] == "same.txt"
    refused = await client.post(f"/api/projects/{manager.project_id}/run", json={"command": "printf should-not-run > forbidden"})
    assert "неприменённые" in refused.text and not (root / "forbidden").exists()
    assert (await client.post(f"/api/projects/{manager.project_id}/command-changes", json={"action": "discard", "change_id": str(uuid4())})).status_code == 409
    files.change_file(root, "same.txt", "original", files.read_file(root, "same.txt")["sha256"])
    applied = (await client.post(f"/api/projects/{manager.project_id}/command-changes", json={"action": "apply", "change_id": packet["id"]})).json()
    assert "error" not in applied, applied
    assert (root / "same.txt").read_text() == "generated"


@pytest.mark.asyncio
async def test_background_generator_imports_binary_to_worktree_then_main(client, db_sessionmaker, bridge):
    manager, _chat, root = await setup(client, db_sessionmaker)
    row = (await manager.start_command(code_workers.CommandStart(
        command="printf hello > result.txt; printf '\\000\\377' > result.bin")))["worker"]
    result = await terminal(manager, row["id"])
    assert result["worker"]["status"] == "done", result
    assert {change["path"] for change in result["worker"]["changes"]} == {"result.txt", "result.bin"}
    assert not (root / "result.txt").exists()
    events = [event async for event in project_agent._apply_worker(manager, {"worker_id": row["id"]},
                                                                 {"id": "apply", "name": "worker_apply"}, None, str(root), set())]
    assert "error" not in events[-1]["outcome"], events
    assert (root / "result.bin").read_bytes() == b"\0\xff"
    assert (root / "result.txt").read_text() == "hello"


@pytest.mark.asyncio
async def test_background_timeout_is_error_and_keeps_partial_files(client, db_sessionmaker, bridge):
    manager, _chat, root = await setup(client, db_sessionmaker)
    ident = (await manager.start_command(code_workers.CommandStart(command="printf partial > partial; sleep 60", timeout=1)))["worker"]["id"]
    result = await terminal(manager, ident)
    assert result["worker"]["status"] == "error" and "время" in result["worker"]["error"]
    assert any(change["path"] == "partial" for change in result["worker"]["changes"])
    assert not (root / "partial").exists()


@pytest.mark.asyncio
async def test_interactive_process_retains_variables_does_not_block_sibling_and_stops(client, db_sessionmaker, bridge):
    manager, chat, root = await setup(client, db_sessionmaker)
    ident = (await manager.start_command(code_workers.CommandStart(command="python3 -u -i", interactive=True)))["worker"]["id"]
    for _ in range(500):
        if (manager.owner_id, ident) in bridge.processes:
            break
        await asyncio.sleep(0.01)
    assert (await manager.output(code_workers.WorkerOutput(worker_id=ident)))["worker"]["detached"]
    await manager.input(code_workers.CommandInput(worker_id=ident, text="number = 41\n"))
    await manager.input(code_workers.CommandInput(worker_id=ident, text="print(number + 1)\nopen('repl.txt','w').write(str(number))\n"))
    sibling = (await manager.start_command(code_workers.CommandStart(command="printf sibling > sibling.txt")))["worker"]["id"]
    assert (await terminal(manager, sibling))["worker"]["status"] == "done"
    assert (manager.owner_id, ident) in bridge.processes
    for _ in range(500):
        if (bridge._workspace(manager.owner_id, ident).path / "repl.txt").exists():
            break
        await asyncio.sleep(0.01)
    assert (bridge._workspace(manager.owner_id, ident).path / "repl.txt").exists()
    for _ in range(200):
        if "42" in (await manager.output(code_workers.WorkerOutput(worker_id=ident)))["output"]:
            break
        await asyncio.sleep(0.01)
    assert "42" in (await manager.output(code_workers.WorkerOutput(worker_id=ident)))["output"]
    await manager.stop(ident)
    stopped = await manager.output(code_workers.WorkerOutput(worker_id=ident))
    assert "42" in stopped["output"] and any(change["path"] == "repl.txt" for change in stopped["worker"]["changes"])
    assert (manager.owner_id, ident) not in bridge.processes
    assert (await client.post(f"/api/chats/{chat['id']}/workers/{ident}/input", json={"text": "print(0)\n"})).status_code == 409
    assert not (root / "repl.txt").exists()


@pytest.mark.asyncio
async def test_chat_terminal_pty_retains_input_and_dimensions_after_reconnect(client, db_sessionmaker, bridge):
    manager, chat, root = await setup(client, db_sessionmaker)
    started = await client.post(f"/api/chats/{chat['id']}/workers/command", json={
        "command": "python3 -u -i", "interactive": True, "detached": True, "tty": True,
        "timeout": 120, "rows": 31, "cols": 93,
    })
    assert started.status_code == 200, started.text
    ident = started.json()["worker"]["id"]
    for _ in range(500):
        if (manager.owner_id, ident) in bridge.processes:
            break
        await asyncio.sleep(0.01)
    sent = await client.post(f"/api/chats/{chat['id']}/workers/{ident}/input", json={
        "text": "import os; print('tty', os.isatty(0), os.get_terminal_size().columns, flush=True)\n", "cols": 107, "rows": 33,
    })
    assert sent.status_code == 200, sent.text
    for _ in range(500):
        detail = await client.get(f"/api/chats/{chat['id']}/workers/{ident}")
        if "tty True 107" in detail.json().get("output", ""):
            break
        await asyncio.sleep(0.01)
    assert "tty True 107" in detail.json()["output"]
    assert not (root / "anything").exists()
    assert (await client.post(f"/api/chats/{chat['id']}/workers/{ident}/stop", json={})).status_code == 200
    assert (manager.owner_id, ident) not in bridge.processes


@pytest.mark.asyncio
async def test_project_terminal_history_and_rollback_are_scoped_to_chat_worktree(client, db_sessionmaker, bridge):
    space, _manager, chat, root = await controller(client, db_sessionmaker)
    created = await space.enter(code_workspace.Enter(name="terminal"))
    copy = Path(created["workspace"]["path"])
    endpoint = f"/api/projects/{space.runtime.project_id}/chats/{chat['id']}/workspaces/{created['workspace']['id']}"
    response = await client.post(endpoint + "/run", json={"command": "printf private > written.txt"})
    assert response.status_code == 200 and '"type": "files"' in response.text, response.text
    assert (copy / "written.txt").read_text() == "private"
    assert not (root / "written.txt").exists()
    history = (await client.get(endpoint + "/shell-history")).json()
    assert len(history) == 1
    assert (await client.get(f"/api/projects/{space.runtime.project_id}/shell-history")).json() == []
    rollback = await client.post(endpoint + f"/shell-history/{history[0]['id']}/rollback")
    assert rollback.status_code == 200, rollback.text
    assert not (copy / "written.txt").exists()


@pytest.mark.asyncio
async def test_command_cancelled_while_saving_output_closes_transport_before_job_ends(client, db_sessionmaker, bridge, monkeypatch):
    manager, _chat, _root = await setup(client, db_sessionmaker)
    saving, retained = asyncio.Event(), []
    save, command = manager._save, code_runner.Runner.command

    async def blocked_save(worker_id, **values):
        if "ready" in values.get("output", "") and "status" not in values:
            saving.set()
            await asyncio.Event().wait()
        await save(worker_id, **values)

    def retained_stream(runner, args):
        stream = command(runner, args)
        retained.append(stream)  # Explicit cleanup must not wait for garbage collection.
        return stream

    monkeypatch.setattr(manager, "_save", blocked_save)
    monkeypatch.setattr(code_runner.Runner, "command", retained_stream)
    ident = (await manager.start_command(code_workers.CommandStart(command="printf partial > partial.txt; printf ready; sleep 60")))["worker"]["id"]
    await asyncio.wait_for(saving.wait(), 10)
    result = await asyncio.wait_for(manager.stop(ident), 10)
    assert (manager.owner_id, ident) not in bridge.processes
    assert "ready" in result["output"]
    assert any(change["path"] == "partial.txt" for change in result["worker"]["changes"])
    for stream in retained:
        await stream.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "permissions"), [("review", None), ("auto", ["files.read", "code.run"])])
async def test_readonly_mode_or_persona_cannot_import_shell_writes(tmp_path, bridge, monkeypatch, mode, permissions):
    _script(monkeypatch, [[{"name": "run_command", "args": {"command": "printf generated > new.txt"}}]])
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    events = [event async for event in project_agent.run(None, "key", "model", [{"role": "user", "content": "check"}],
                                                       str(tmp_path), permissions, runner=runner, mode=mode)]
    assert not (tmp_path / "new.txt").exists()
    assert any(event.get("tool", {}).get("status") == "error" for event in events)
    assert (await code_shell.fetch(runner))["id"]
