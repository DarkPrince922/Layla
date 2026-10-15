"""Real stdio language servers and adversarial protocol/lifecycle checks."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys

import pytest

from layla_sandbox import lsp, server


class Workspace:
    """Same spawn interface, without uid switching in a developer's test runner."""
    def __init__(self, path, uid=1, project="project"):
        self.path, self.uid, self.project = path, uid, project

    def _wrap(self, argv):
        return argv

    def _as_user(self):
        return {}

    def env(self):
        return {"PATH": os.environ.get("PATH", ""), "HOME": str(self.path), "LANG": "C.UTF-8",
                "PYTHONUNBUFFERED": "1"}


def body(path="app.py", text="", operation="documentSymbol", **extra):
    return server.LspIn(path=path, text=text, operation=operation, **extra)


async def test_real_python_definitions_references_hover_symbols_and_fresh_diagnostics(tmp_path, monkeypatch):
    pytest.importorskip("pylsp")
    monkeypatch.setitem(lsp.COMMANDS, "python", [sys.executable, "-I", "-m", "pylsp"])
    text = "def greet(name):\n    return name\n\ngreet('world')\n"
    (tmp_path / "app.py").write_text(text)
    ws, registry = Workspace(tmp_path), lsp.Registry()
    try:
        definition = await registry.query(ws, body(text=text, operation="goToDefinition", line=4, character=2))
        assert definition["result"][0]["path"] == "app.py"
        assert definition["result"][0]["range"]["start"]["line"] == 0
        unicode_text = "def greet(name):\n    return name\n\nmarker = '😀'; greet('world')\n"
        unicode_definition = await registry.query(ws, body(text=unicode_text, operation="goToDefinition", line=4,
                                                          character=len("marker = '😀'; ".encode("utf-16-le")) // 2 + 5))
        assert unicode_definition["result"][0]["range"]["start"]["line"] == 0
        (tmp_path / "helpers.py").write_text("def helper():\n    return 1\n")
        imported = "from helpers import helper\nhelper()\n"
        (tmp_path / "entry.py").write_text(imported)
        cross_file = await registry.query(ws, body("entry.py", imported, "goToDefinition", line=2, character=2))
        assert cross_file["result"][0]["path"] == "helpers.py"
        references = await registry.query(ws, body(text=text, operation="findReferences", line=1, character=6))
        assert len(references["result"]) >= 2
        hover = await registry.query(ws, body(text=text, operation="hover", line=1, character=6))
        assert "greet" in json.dumps(hover["result"])
        symbols = await registry.query(ws, body(text=text))
        assert "greet" in json.dumps(symbols["result"])
        error_text = "unknown_name()\n"
        bad = await registry.query(ws, body(text=error_text, operation="diagnostics"))
        assert bad["result"]["ready"] and any("undefined" in item["message"] for item in bad["result"]["items"])
        good = await registry.query(ws, body(text="print('ok')\n", operation="diagnostics"))
        assert good["result"]["ready"] and not good["result"]["items"]
        assert good["result"]["version"] > bad["result"]["version"]
        unicode_diagnostics = await registry.query(ws, body(text="'😀'; missing_name()\n", operation="diagnostics"))
        assert unicode_diagnostics["position_encoding"] == "utf-32"
        assert unicode_diagnostics["result"]["items"][0]["range"]["start"]["character"] == 5
        syntax = await registry.query(ws, body(text="def bad(:\n", operation="diagnostics"))
        assert syntax["result"]["ready"] and syntax["result"]["items"][0]["range"]["start"]["character"] == 8
        session = next(iter(registry.items.values()))
        pid = session.proc.pid
        assert session.documents
        await registry.changed(ws.uid, ws.project, {"app.py"}, [])
        assert (tmp_path / "app.py").as_uri() not in session.documents
        await registry.query(ws, body(text=text))
        session.proc.kill()
        await session.proc.wait()
        restarted = await registry.query(ws, body(text=text))
        assert "greet" in json.dumps(restarted["result"])
        assert next(iter(registry.items.values())).proc.pid != pid
    finally:
        await registry.shutdown()
    assert not registry.items


async def test_real_typescript_implementation_call_hierarchy_and_diagnostics(tmp_path, monkeypatch):
    if not os.path.isdir("/proc"):
        pytest.skip("Node requires procfs on this restricted developer host; CI has procfs")
    command = os.environ.get("LAYLA_TEST_TS_SERVER") or shutil.which("typescript-language-server")
    if not command:
        pytest.skip("typescript-language-server not installed; CI installs the pinned server")
    monkeypatch.setitem(lsp.COMMANDS, "typescript", [command, "--stdio"])
    text = ("interface Animal { sound(): string }\n"
            "class Cat implements Animal { sound() { return 'meow'; } }\n"
            "function greet() { return new Cat().sound(); }\n"
            "greet();\nconst broken: number = 'text';\n")
    (tmp_path / "app.ts").write_text(text)
    registry, ws = lsp.Registry(), Workspace(tmp_path)
    try:
        for operation in ("goToDefinition", "findReferences", "hover", "prepareCallHierarchy", "incomingCalls", "outgoingCalls"):
            result = await registry.query(ws, body("app.ts", text, operation, line=3, character=11))
            assert result["result"], (operation, result)
        implementation = await registry.query(ws, body("app.ts", text, "goToImplementation", line=1, character=12))
        assert implementation["result"][0]["path"] == "app.ts"
        for operation in ("documentSymbol", "workspaceSymbol"):
            result = await registry.query(ws, body("app.ts", text, operation, query="greet"))
            assert "greet" in json.dumps(result["result"])
        diagnostics = await registry.query(ws, body("app.ts", text, "diagnostics"))
        assert diagnostics["result"]["ready"] and any(item.get("code") == 2322 for item in diagnostics["result"]["items"])
    finally:
        await registry.shutdown()


FAKE = '''import json, sys, time
def send(obj):
    data=json.dumps(dict(jsonrpc="2.0", **obj)).encode()
    sys.stdout.buffer.write(("Content-Length: %d\\r\\n\\r\\n"%len(data)).encode()+data)
    sys.stdout.buffer.flush()
while True:
    head={}
    while True:
        line=sys.stdin.buffer.readline()
        if not line: sys.exit(0)
        if line==b"\\r\\n": break
        k,v=line.split(b":",1); head[k.lower()]=v
    msg=json.loads(sys.stdin.buffer.read(int(head[b"content-length"])))
    method=msg.get("method")
    if method=="initialize":
        send(dict(id=msg["id"], result=dict(capabilities=dict(documentSymbolProvider=True))))
    elif method=="textDocument/didOpen":
        send(dict(id=900, method="workspace/applyEdit", params=dict(edit={})))
    elif msg.get("id")==900:
        assert msg["result"]["applied"] is False
    elif method=="textDocument/documentSymbol":
        time.sleep(DELAY)
        send(dict(id=msg["id"], result=[dict(name="safe",kind=12,range=dict(start=dict(line=0,character=0),end=dict(line=0,character=1)))]))
    elif method=="shutdown":
        send(dict(id=msg["id"],result=None))
    elif method=="exit": sys.exit(0)
'''


def fake(tmp_path, monkeypatch, delay=0):
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE.replace("DELAY", str(delay)))
    monkeypatch.setitem(lsp.COMMANDS, "python", [sys.executable, "-I", str(script)])


async def test_unsupported_operation_uid_project_isolation_limits_and_reaping(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch)
    registry = lsp.Registry()
    try:
        with pytest.raises(lsp.LspError, match="не поддерживает"):
            await registry.query(Workspace(tmp_path), body(operation="goToDefinition"))
        for uid, project in [(1, "project"), (1, "other"), (2, "project")]:
            result = await registry.query(Workspace(tmp_path, uid, project), body())
            assert result["result"][0]["name"] == "safe"
        assert len(registry.items) == 3
        await registry.stop(1, "other")
        assert set(registry.items) == {(1, "project", "python"), (2, "project", "python")}
        monkeypatch.setattr(lsp, "MAX_PER_USER", 1)
        with pytest.raises(lsp.LspError, match="лимит"):
            await registry.query(Workspace(tmp_path, 1, "third"), body())
        for session in registry.items.values():
            session.touched -= lsp.IDLE + 1
        await registry.reap()
        assert not registry.items
    finally:
        await registry.shutdown()


async def test_cancel_closes_session_and_does_not_leave_processes(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, delay=60)
    registry, ws = lsp.Registry(), Workspace(tmp_path)
    task = asyncio.create_task(registry.query(ws, body()))
    for _ in range(300):
        if registry.items:
            break
        await asyncio.sleep(0.01)
    session = next(iter(registry.items.values()))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.proc.returncode is not None and not registry.items


async def test_timeout_removes_pending_request_and_malformed_frame_fails_start(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch)
    registry, ws = lsp.Registry(), Workspace(tmp_path)
    try:
        await registry.query(ws, body())
        session = next(iter(registry.items.values()))
        with pytest.raises(TimeoutError):
            await session.ask("unknownMethod", {}, timeout=0.02)
        assert not session.pending
        assert (await registry.query(ws, body()))["result"]
    finally:
        await registry.shutdown()
    script = tmp_path / "malformed.py"
    script.write_text("import sys\nsys.stdout.buffer.write(b'Content-Length: 90000000\\r\\n\\r\\n')\nsys.stdout.buffer.flush()\nsys.stdin.buffer.read()\n")
    monkeypatch.setitem(lsp.COMMANDS, "python", [sys.executable, "-I", str(script)])
    with pytest.raises(lsp.LspError, match="прервана"):
        await registry.query(ws, body())
    assert not registry.items


def test_external_encoded_paths_and_oversized_results_are_filtered(tmp_path):
    location = lambda uri: {"uri": uri, "range": {"start": {"line": 0, "character": 0}}}
    data = [location((tmp_path / "ok.py").as_uri()), location("file:///etc/passwd"),
            location(tmp_path.as_uri() + "/%2e%2e/secret.py"), location("https://example.org/private"),
            location((tmp_path / "node_modules/x.ts").as_uri())]
    result = lsp.normalize(data, tmp_path, [1000])
    assert len(result) == 1 and result[0]["path"] == "ok.py"
    assert len(lsp.normalize(["x"] * 1000, tmp_path, [1000])) == 100
