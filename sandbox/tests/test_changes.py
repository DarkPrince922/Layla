from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil

from layla_sandbox import config, server
from layla_sandbox.changes import scan
from tests.test_sandbox import client, root_only, sync


def test_scan_binary_modes_deletion_and_exclusions(tmp_path):
    (tmp_path / "before").write_bytes(b"old\r\n")
    (tmp_path / "gone").write_text("gone")
    before = scan(str(tmp_path))["files"]
    (tmp_path / "before").write_bytes(b"new\r\n")
    (tmp_path / "before").chmod(0o755)
    (tmp_path / "gone").unlink()
    (tmp_path / "asset").write_bytes(b"\xff\0")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules/ignored").write_text("dependency")
    result = scan(str(tmp_path), before)
    assert {item["path"] for item in result["changes"]} == {"before", "gone", "asset"}
    assert base64.b64decode(next(item for item in result["changes"] if item["path"] == "asset")["data"]) == b"\xff\0"
    assert next(item for item in result["changes"] if item["path"] == "before")["mode"] == 0o755
    assert next(item for item in result["changes"] if item["path"] == "gone")["after_sha256"] is None


def test_scan_does_not_import_link_replacement_or_hardlinks(tmp_path):
    (tmp_path / "file").write_text("old")
    (tmp_path / "directory").mkdir()
    (tmp_path / "directory/nested").write_text("nested")
    before = scan(str(tmp_path))["files"]
    (tmp_path / "file").unlink()
    (tmp_path / "file").symlink_to("/etc/passwd")
    (tmp_path / "directory/nested").unlink()
    (tmp_path / "directory").rmdir()
    (tmp_path / "directory").symlink_to("/etc")
    (tmp_path / "hard").write_text("local")
    os.link(tmp_path / "hard", tmp_path / "hard-again")
    result = scan(str(tmp_path), before)
    assert result["changes"] == []
    assert {"file", "directory", "directory/nested", "hard", "hard-again"} <= set(result["blocked"])


@root_only
async def test_uid_export_pending_survives_sync_and_requires_correct_ack(work):
    async with client() as http:
        await sync(http, "alice", "p1", {"old": "old", "gone": "gone"})
        response = await http.post("/workspaces/alice/p1/run", json={"command": "printf new > old; rm gone; printf '\\000\\377' > new.bin", "collect_changes": True})
        events = [json.loads(line) for line in response.text.splitlines()]
        packet = events[-1]
        assert packet["type"] == "changes", events
        assert {item["path"] for item in packet["changes"]} == {"old", "gone", "new.bin"}
        assert base64.b64decode(next(item for item in packet["changes"] if item["path"] == "new.bin")["data"]) == b"\0\xff"
        denied = await http.post("/workspaces/alice/p1/sync", content=json.dumps({"files": {}}).encode() + b"\n")
        assert denied.status_code == 409
        retained = (await http.get("/workspaces/alice/p1/changes")).json()
        assert retained["id"] == packet["id"] and retained["changes"] == packet["changes"]
        assert (await http.get("/workspaces/bob/p1/changes")).json()["id"] is None
        assert (await http.delete("/workspaces/alice/p1/changes/wrong")).status_code == 409
        assert (await http.delete("/workspaces/alice/p1/changes/" + packet["id"])).status_code == 200
        assert (await http.get("/workspaces/alice/p1/changes")).json()["id"] is None


@root_only
async def test_timeout_preserves_partial_file_and_reaps_children(work):
    async with client() as http:
        await sync(http, "alice", "p1", {})
        response = await http.post("/workspaces/alice/p1/run", json={"command": "printf partial > partial.txt; (sleep 60 &) ; sleep 60", "collect_changes": True, "timeout": 1})
        events = [json.loads(line) for line in response.text.splitlines()]
        assert next(item for item in events if item["type"] == "exit")["timed_out"]
        assert events[-1]["changes"][0]["path"] == "partial.txt"
        assert not server.processes


@root_only
async def test_interactive_stdin_eof_and_sibling_workspace_are_independent(work):
    async with client() as http:
        await sync(http, "alice", "p1", {})
        await sync(http, "alice", "p2", {})
        run = asyncio.create_task(http.post("/workspaces/alice/p1/run", json={"command": "read line; printf '%s' \"$line\"; cat", "interactive": True, "collect_changes": True}))
        for _ in range(1000):
            if ("alice", "p1") in server.processes:
                break
            await asyncio.sleep(0.01)
        assert ("alice", "p1") in server.processes
        other = await asyncio.wait_for(http.post("/workspaces/alice/p2/run", json={"command": "echo sibling"}), 30)
        assert "sibling" in other.text and not run.done()
        assert (await http.post("/workspaces/alice/p1/input", json={"text": "hello\nworld", "eof": True})).status_code == 200
        result = await asyncio.wait_for(run, 10)
        assert "hello" in result.text and "world" in result.text
        assert (await http.post("/workspaces/alice/p1/input", json={"text": "late"})).status_code == 409


@root_only
async def test_relative_cwd_and_conditional_powershell_backend(work):
    async with client() as http:
        await sync(http, "alice", "p1", {"sub/input.txt": "inside"})
        result = await http.post("/workspaces/alice/p1/run", json={"command": "cat input.txt > output.txt", "cwd": "sub", "collect_changes": True})
        packet = json.loads(result.text.splitlines()[-1])
        assert packet["changes"][0]["path"] == "sub/output.txt"
        assert base64.b64decode(packet["changes"][0]["data"]) == b"inside"
        await http.delete("/workspaces/alice/p1/changes/" + packet["id"])
        powershell = await http.post("/workspaces/alice/p1/run", json={"command": "Set-Content -Path pwsh.txt -Value verified", "shell": "powershell", "collect_changes": True})
        if shutil.which("pwsh", path=config.BASE_PATH):
            events = [json.loads(line) for line in powershell.text.splitlines()]
            assert next(event for event in events if event["type"] == "exit")["code"] == 0
            assert any(change["path"] == "pwsh.txt" for change in events[-1]["changes"])
        else:
            assert powershell.status_code == 422 and "PowerShell" in powershell.text
