"""Core no-follow source access, owner isolation and the agent LSP tool path."""
from __future__ import annotations

import json
from pathlib import Path

from app.models.user import Project
from app.services import code_lsp, files, project_agent, sandbox


async def setup(client, maker):
    registered = await client.post("/api/auth/register", json={"email": "lsp@example.com", "password": "hunter2hunter2"})
    project = (await client.post("/api/projects", json={"name": "LSP"})).json()
    async with maker() as session:
        root = Path((await session.get(Project, project["id"])).path)
    return registered.json()["id"], project["id"], root


async def test_editor_analysis_is_readonly_and_bound_to_owner_project(client, db_sessionmaker, monkeypatch):
    owner, project, root = await setup(client, db_sessionmaker)
    (root / "app.py").write_text("print('saved')\n")
    synced, calls = [], []

    async def sync(*args):
        synced.append(args)

    async def transport(lsp, method, payload=None):
        calls.append((lsp.owner_id, lsp.project_id, method, payload))
        if method != "POST":
            return {"available": {"python": {"extensions": [".py"]}}, "sessions": []}
        return {"operation": payload["operation"], "path": payload["path"], "position_encoding": "utf-32", "result": [
            {"path": "app.py", "range": {"start": {"line": 0, "character": 5}}},
            {"path": "../secret", "range": {}}, {"path": "/etc/passwd"}, {"path": "missing.py"},
        ]}

    monkeypatch.setattr(sandbox, "sync", sync)
    monkeypatch.setattr(code_lsp.Client, "transport", transport)
    result = await client.post(f"/api/projects/{project}/lsp", json={
        "operation": "documentSymbol", "path": "app.py", "content": "'😀'; print('draft')\n",
    })
    assert result.status_code == 200, result.text
    assert result.json()["unsaved"] and not result.json()["stale_source"]
    assert len(result.json()["result"]) == 1
    assert result.json()["position_encoding"] == "utf-16"
    assert result.json()["result"][0]["range"]["start"]["character"] == 6
    assert synced == [(owner, project, str(root))]
    assert calls[0][0:3] == (owner, project, "POST")
    assert calls[0][3]["text"] == "'😀'; print('draft')\n"
    assert (root / "app.py").read_text() == "print('saved')\n"
    assert (await client.get(f"/api/projects/{project}/lsp")).status_code == 200
    assert (await client.delete(f"/api/projects/{project}/lsp")).status_code == 200
    await client.post("/api/auth/register", json={"email": "other-lsp@example.com", "password": "hunter2hunter2"})
    for method in (client.get, client.post, client.delete):
        extra = {"json": {"operation": "diagnostics", "path": "app.py"}} if method == client.post else {}
        assert (await method(f"/api/projects/{project}/lsp", **extra)).status_code == 404
    assert len(calls) == 3


async def test_invalid_paths_links_positions_and_tool_arguments_never_reach_sandbox(client, db_sessionmaker, monkeypatch):
    owner, project, root = await setup(client, db_sessionmaker)
    (root / "app.py").write_text("'😀'; print('x')\n")
    (root / "link.py").symlink_to(root / "app.py")

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("must validate before syncing or calling sandbox")

    monkeypatch.setattr(sandbox, "sync", forbidden)
    for path in ("../app.py", "/etc/passwd", "link.py", ".git/config", "missing.py"):
        response = await client.post(f"/api/projects/{project}/lsp", json={"operation": "diagnostics", "path": path})
        assert response.status_code == 422, (path, response.text)
    for position in ({"line": 100, "character": 1}, {"line": 1, "character": 100}):
        response = await client.post(f"/api/projects/{project}/lsp", json={"operation": "hover", "path": "app.py", **position})
        assert response.status_code == 422
    invalid = await client.post(f"/api/projects/{project}/lsp", json={"operation": "workspace/executeCommand", "path": "app.py"})
    assert invalid.status_code == 422
    assert await code_lsp.Client(owner, project, str(root)).tools([]) == []
    code_lsp.validate_position("😀x\n", code_lsp.Query(operation="hover", path="app.py", line=1, character=4))
    converted = code_lsp.utf16_ranges(str(root), {"range": {"start": {"line": 0, "character": 5}}}, "app.py", {"app.py": "'😀'; missing()"})
    assert converted["range"]["start"]["character"] == 6


async def test_agent_lsp_in_plan_uses_saved_source_without_approval(client, db_sessionmaker, monkeypatch):
    owner, project, root = await setup(client, db_sessionmaker)
    files.change_file(root, "app.py", "def greet():\n    return 42\n", None)
    turns = 0

    async def info(**_kwargs):
        return {"lsp": {"python": {"extensions": [".py"]}}}

    async def sync(*_args):
        pass

    async def transport(_lsp, method, payload=None):
        assert method == "POST" and payload["text"].startswith("def greet")
        return {"operation": payload["operation"], "path": payload["path"], "result": [{"name": "greet"}]}

    async def stream(_p, _k, _m, conversation, available, **_kwargs):
        nonlocal turns
        assert "lsp" in {item["function"]["name"] for item in available}
        turns += 1
        if turns == 1:
            yield "tool_calls", [{"id": "lsp", "type": "function", "function": {"name": "lsp", "arguments": json.dumps({"operation": "documentSymbol", "path": "app.py"})}}]
        else:
            assert "greet" in conversation[-1]["content"]
            yield "content", "Found greet."

    async def forbidden(_event):
        raise AssertionError("LSP read analysis does not require approval")

    monkeypatch.setattr(sandbox, "info", info)
    monkeypatch.setattr(sandbox, "sync", sync)
    monkeypatch.setattr(code_lsp.Client, "transport", transport)
    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    events = [event async for event in project_agent.run(
        object(), "key", "model", [{"role": "user", "content": "Find symbols"}], str(root),
        ["files.read"], mode="plan", approve=forbidden, lsp=code_lsp.Client(owner, project, str(root)),
    )]
    assert turns == 2 and any(event.get("tool", {}).get("status") == "done" for event in events)
    assert not any("checkpoint" in event for event in events)


async def test_unavailable_backend_hides_tool_and_returns_clear_api_error(client, db_sessionmaker, monkeypatch):
    owner, project, root = await setup(client, db_sessionmaker)
    (root / "app.py").write_text("print(42)\n")

    async def unavailable(**_kwargs):
        return None

    async def fail(*_args, **_kwargs):
        raise sandbox.SandboxError("Песочница не настроена")

    monkeypatch.setattr(sandbox, "info", unavailable)
    monkeypatch.setattr(sandbox, "sync", fail)
    assert await code_lsp.Client(owner, project, str(root)).tools() == []
    result = await client.post(f"/api/projects/{project}/lsp", json={"operation": "diagnostics", "path": "app.py"})
    assert result.status_code == 503 and "не настроена" in result.json()["detail"]
