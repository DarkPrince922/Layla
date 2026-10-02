"""Запуск кода: run_command (песочница проекта) и run_code (Piston) — в ходе агента и из интерфейса."""
from __future__ import annotations

import asyncio
import io
import json
import tarfile

import httpx
import pytest

from app.models.chat import Message
from app.services import code_runner, history, project_agent, sandbox

pytestmark = pytest.mark.asyncio

BOX = {"tools": {"python": "Python 3.12.1", "node": "v22.1.0"}, "network": "proxy",
       "limits": {"max_timeout": 900}}
RUNTIMES = [{"language": "go", "version": "1.16.2", "aliases": ["golang"]},
            {"language": "rust", "version": "1.68.2", "aliases": ["rs"]}]


def _script(monkeypatch, turns: list[list[dict]], seen: list | None = None):
    state = {"turn": 0}

    async def stream_turn(provider, key, model, conversation, available, **kw):
        if seen is not None:
            seen.append({"tools": {t["function"]["name"] for t in available},
                         "specs": {t["function"]["name"]: t for t in available},
                         "conversation": list(conversation)})
        calls = turns[state["turn"]] if state["turn"] < len(turns) else []
        state["turn"] += 1
        if calls:
            yield ("tool_calls", [
                {"id": f"c{i}", "type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                for i, c in enumerate(calls)])
        else:
            yield ("content", "Готово")

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream_turn)


def _services(monkeypatch, *, box=BOX, runtimes=RUNTIMES, events=None, calls=None):
    async def info(**kw):
        return box

    async def fake_runtimes(**kw):
        return runtimes

    async def run(owner_id, project_id, root, command, *, timeout=120, stdin=None):
        if calls is not None:
            calls.append({"owner": owner_id, "project": project_id, "command": command, "timeout": timeout})
        for event in events or [
            {"type": "info", "data": "Синхронизирую файлы проекта…"},
            {"type": "output", "data": "collected 3 items\n"},
            {"type": "output", "data": "\x1b[32m3 passed\x1b[0m in 0.1s\n"},
            {"type": "exit", "code": 0, "signal": None, "duration": 1.2, "timed_out": False, "truncated": False},
        ]:
            yield event

    monkeypatch.setattr(sandbox, "info", info)
    monkeypatch.setattr(sandbox, "runtimes", fake_runtimes)
    monkeypatch.setattr(sandbox, "run", run)


async def _events(root, messages=None, **kw):
    out = []
    runner = code_runner.Runner("user-1", "proj-1", str(root))
    async for event in project_agent.run(None, "key", "m", messages or [{"role": "user", "content": "проверь"}],
                                         str(root), runner=runner, **kw):
        out.append(event)
    return out


async def test_run_command_streams_output_and_reports_to_model(tmp_path, monkeypatch):
    seen, calls = [], []
    _services(monkeypatch, calls=calls)
    _script(monkeypatch, [[{"name": "run_command", "args": {"command": "pytest -q", "timeout": 60}}]], seen)
    events = await _events(tmp_path)
    tools = [e["tool"] for e in events if "tool" in e]
    assert tools[0]["status"] == "running" and tools[0]["command"] == "pytest -q"
    final = tools[-1]
    assert final["status"] == "done" and final["exit_code"] == 0 and final["duration_s"] == 1.2
    assert "3 passed" in final["output"] and "\x1b" not in final["output"]  # без цветовых кодов
    assert calls == [{"owner": "user-1", "project": "proj-1", "command": "pytest -q", "timeout": 60}]
    # Модель получила итог команды, но не копию вывода для карточки.
    reply = json.loads(seen[1]["conversation"][-1]["content"])
    assert reply["exit_code"] == 0 and "3 passed" in reply["output"] and "shown" not in reply
    # Описание инструмента говорит, что есть в песочнице и что с сетью.
    spec = seen[0]["specs"]["run_command"]["function"]["description"]
    assert "Python 3.12.1" in spec and "package registries" in spec
    assert "NOT saved to the project" in spec
    assert "run_command executes bash" in seen[0]["conversation"][0]["content"]


async def test_failing_command_and_sandbox_errors(tmp_path, monkeypatch):
    seen = []
    _services(monkeypatch, events=[
        {"type": "output", "data": "E   AssertionError: 2 != 3\n" + "x" * 20000 + "\nFAILED test_a.py\n"},
        {"type": "exit", "code": 1, "signal": None, "duration": 0.5, "timed_out": False, "truncated": False}])
    _script(monkeypatch, [[{"name": "run_command", "args": {"command": "pytest"}}]], seen)
    events = await _events(tmp_path)
    final = [e["tool"] for e in events if "tool" in e][-1]
    assert final["status"] == "done" and final["exit_code"] == 1
    reply = json.loads(seen[1]["conversation"][-1]["content"])
    assert reply["output_truncated"] is True and reply["output"].rstrip().endswith("FAILED test_a.py")
    assert len(reply["output"]) < 9000

    async def broken(*a, **kw):
        raise sandbox.SandboxError("Песочница не запущена")
        yield  # pragma: no cover

    monkeypatch.setattr(sandbox, "run", broken)
    seen.clear()
    _script(monkeypatch, [[{"name": "run_command", "args": {"command": "ls"}}]], seen)
    events = await _events(tmp_path)
    final = [e["tool"] for e in events if "tool" in e][-1]
    assert final["status"] == "error" and "не запущена" in final["error"]


async def test_run_code_uses_project_files_in_piston(tmp_path, monkeypatch):
    (tmp_path / "main.go").write_text('package main\nfunc main(){ println("hi") }\n')
    sent = {}

    async def execute(language, version, sources, *, stdin="", args=None):
        sent.update(language=language, sources=sources)
        return {"language": "go", "version": "1.16.2",
                "compile": {"output": "", "code": 0}, "run": {"output": "hi\n", "code": 0, "signal": None}}

    _services(monkeypatch)
    monkeypatch.setattr(sandbox, "execute", execute)
    seen = []
    _script(monkeypatch, [[{"name": "run_code", "args": {"language": "go", "paths": ["main.go"]}}]], seen)
    events = await _events(tmp_path)
    final = [e["tool"] for e in events if "tool" in e][-1]
    assert final["status"] == "done" and final["exit_code"] == 0 and "hi" in final["output"]
    assert final["command"] == "go: main.go"
    assert sent["sources"][0]["name"] == "main.go" and "println" in sent["sources"][0]["content"]
    assert "go 1.16.2" in seen[0]["specs"]["run_code"]["function"]["description"]
    # Путь из проекта — только внутри проекта.
    seen.clear()
    _script(monkeypatch, [[{"name": "run_code", "args": {"language": "go", "paths": ["../x.go"]}}]], seen)
    final = [e["tool"] for e in await _events(tmp_path) if "tool" in e][-1]
    assert final["status"] == "error"


async def test_run_tools_follow_permissions_modes_and_availability(tmp_path, monkeypatch):
    _services(monkeypatch)
    seen = []
    _script(monkeypatch, [], seen)
    await _events(tmp_path, permissions=["files.read"])
    assert not seen[-1]["tools"] & code_runner.RUN_TOOLS
    assert "cannot execute commands" in seen[-1]["conversation"][0]["content"]
    await _events(tmp_path, permissions=["files.read", "shell.local"])  # прежнее имя права
    assert code_runner.RUN_TOOLS <= seen[-1]["tools"]
    await _events(tmp_path, mode="plan")
    assert not seen[-1]["tools"] & code_runner.RUN_TOOLS
    await _events(tmp_path, mode="review")  # ревью может прогнать тесты, но не менять файлы
    assert code_runner.RUN_TOOLS <= seen[-1]["tools"] and "write_file" not in seen[-1]["tools"]
    _services(monkeypatch, box=None, runtimes=None)
    await _events(tmp_path)
    assert not seen[-1]["tools"] & code_runner.RUN_TOOLS


async def test_confirm_mode_asks_before_running(tmp_path, monkeypatch):
    calls = []
    _services(monkeypatch, calls=calls)
    _script(monkeypatch, [[{"name": "run_command", "args": {"command": "rm -rf node_modules"}}]])
    asked = []

    async def approve(pending):
        asked.append(pending)
        return "reject"

    events = await _events(tmp_path, mode="confirm", approve=approve)
    assert asked[0]["command"] == "rm -rf node_modules" and asked[0]["status"] == "pending"
    assert [e["tool"]["status"] for e in events if "tool" in e][-1] == "rejected"
    assert calls == []


async def _setup(client, email="runner@example.com"):
    await client.post("/api/auth/register", json={"email": email, "password": "hunter2hunter2"})
    await client.post("/api/providers", json={"name": "M", "kind": "openai_compatible",
                                              "base_url": "http://p/v1", "default_model": "m", "active": True})


async def _wait(client, job_id, predicate, tries=500):
    body = {}
    for _ in range(tries):
        await asyncio.sleep(0.02)
        body = (await client.get(f"/api/jobs/{job_id}")).json()
        if predicate(body):
            return body
    return body


async def test_chat_worker_saves_run_card_and_steps(client, monkeypatch):
    await _setup(client)
    _services(monkeypatch)
    _script(monkeypatch, [[{"name": "run_command", "args": {"command": "npm test"}}]])
    chat = (await client.post("/api/chats", json={"domain": "code", "model": "m"})).json()
    job = (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "проверь", "mode": "auto"})).json()
    done = await _wait(client, job["id"], lambda b: b["status"] in ("done", "error"))
    assert done["status"] == "done", done
    assert any("Команда: npm test — код выхода 0" in s["text"] for s in done["steps"])
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    card = detail["messages"][-1]["meta"]["tools"][0]
    assert card["name"] == "run_command" and card["exit_code"] == 0 and "3 passed" in card["output"]
    audit = (await client.get("/api/audit")).json()
    rows = audit["items"] if isinstance(audit, dict) else audit
    assert any(r["action"] == "project.agent.run" for r in rows)


async def test_terminal_endpoint_streams_and_status_reports_services(client, monkeypatch):
    await _setup(client, "term@example.com")
    project = (await client.post("/api/projects", json={"name": "demo"})).json()
    status = (await client.get("/api/sandbox/status")).json()
    assert status == {"sandbox": {"available": False}, "piston": {"available": False, "runtimes": []}}
    calls = []
    _services(monkeypatch, calls=calls)
    status = (await client.get("/api/sandbox/status")).json()
    assert status["sandbox"]["available"] and status["sandbox"]["tools"]["node"] == "v22.1.0"
    assert [r["language"] for r in status["piston"]["runtimes"]] == ["go", "rust"]
    response = await client.post(f"/api/projects/{project['id']}/run", json={"command": "pytest -q"})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["info", "output", "output", "exit"]
    assert events[2]["data"] == "3 passed in 0.1s\n"
    assert calls[0]["project"] == project["id"]
    other = await client.post("/api/projects/nope/run", json={"command": "ls"})
    assert other.status_code == 404


async def test_language_install_is_admin_only(client, monkeypatch, db_sessionmaker):
    from sqlalchemy import select

    from app.models.user import User

    await _setup(client, "first@example.com")
    async with db_sessionmaker() as session:
        (await session.scalar(select(User).where(User.email == "first@example.com"))).is_admin = True
        await session.commit()
    changed = []

    async def change_package(language, version, *, install):
        changed.append((language, version, install))
        return {"language": language, "version": "1.16.2"}

    monkeypatch.setattr(sandbox, "change_package", change_package)
    job = await client.post("/api/sandbox/languages", json={"language": "go", "version": "1.16.2"})
    assert job.status_code == 202, job.text
    done = await _wait(client, job.json()["id"], lambda b: b["status"] in ("done", "error"))
    assert done["status"] == "done" and changed == [("go", "1.16.2", True)]
    await client.post("/api/auth/logout")
    await client.post("/api/auth/register", json={"email": "second@example.com", "password": "hunter2hunter2"})
    denied = await client.post("/api/sandbox/languages", json={"language": "go", "version": "1.16.2"})
    assert denied.status_code == 403


def test_manifest_skips_dependencies_and_pack_is_project_only(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print(1)")
    for skipped in ("node_modules", ".venv", ".git", "__pycache__"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "junk.txt").write_text("x")
    (tmp_path / "link.py").symlink_to(tmp_path / "src" / "app.py")
    marks = sandbox.manifest(str(tmp_path))
    assert list(marks) == ["src/app.py"]
    with tarfile.open(fileobj=io.BytesIO(sandbox.pack(str(tmp_path), ["src/app.py"]))) as archive:
        assert archive.getnames() == ["src/app.py"]
    with pytest.raises(ValueError):
        sandbox.pack(str(tmp_path), ["../etc/passwd"])


async def test_sync_sends_only_what_the_sandbox_needs(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("a")
    (tmp_path / "b.py").write_text("b")
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        head, _, archive = request.content.partition(b"\n")
        bodies.append((json.loads(head)["files"], archive))
        if not archive:
            return httpx.Response(200, json={"need": ["b.py"]})
        return httpx.Response(200, json={"ok": True, "written": 1, "removed": 0})

    monkeypatch.setattr(sandbox, "_client", lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sandbox"))
    await sandbox.sync("u-1", "p-1", str(tmp_path))
    assert set(bodies[0][0]) == {"a.py", "b.py"} and bodies[0][1] == b""
    with tarfile.open(fileobj=io.BytesIO(bodies[1][1])) as archive:
        assert archive.getnames() == ["b.py"]


def test_history_mentions_commands_and_shrinks_long_output():
    message = Message(chat_id="c", role="assistant", content="Проверил.", meta={"tools": [
        {"name": "run_command", "command": "pytest -q", "status": "done", "exit_code": 1},
        {"name": "run_code", "command": "go: main.go", "status": "error", "error": "Piston недоступен"}]})
    text = history.entry(message)["content"]
    assert "`pytest -q` → exit 1" in text and "`go: main.go` → error: Piston недоступен" in text
    long = json.dumps({"exit_code": 1, "output": "start " + "x" * 5000 + " the error at the end"})
    slim = history._shrink_tool({"role": "tool", "content": long})
    assert json.loads(slim["content"])["output"].endswith("the error at the end")


def test_output_helpers():
    assert sandbox.clean("\x1b[31mred\x1b[0m\n50%\r100% done\r\nok") == "red\n100% done\nok"
    text, cut = sandbox.squeeze("a" * 100 + "END", 50)
    assert cut and text.endswith("END") and "пропущено" in text


async def test_sync_connection_failure_is_a_sandbox_error(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("a")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(sandbox, "_client", lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sandbox"))
    with pytest.raises(sandbox.SandboxError, match="не отвечает"):
        await sandbox.sync("u-1", "p-1", str(tmp_path))
    runner = code_runner.Runner("u-1", "p-1", str(tmp_path))
    items = [item async for item in runner.command(code_runner.RunCommand(command="ls"))]
    assert "не отвечает" in items[-1]["result"]["error"]
