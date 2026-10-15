"""Explicit Code workspace binding and transcript transfer use owner-scoped state."""
import json
from pathlib import Path

import pytest

from app.services import code_session_transfer, code_workspace, gitops
from tests.test_code_workspaces import controller


async def test_explicit_editor_target_and_git_history_follow_private_copy(client, db_sessionmaker):
    space, _manager, _chat, root = await controller(client, db_sessionmaker)
    (root / "file.txt").write_text("source")
    created = await space.enter(code_workspace.Enter(name="editable"))
    ident = created["workspace"]["id"]
    route = f"/api/projects/{space.runtime.project_id}/chats/{_chat['id']}/workspaces/{ident}"
    original = f"/api/projects/{space.runtime.project_id}"
    read = (await client.get(f"{route}/file?path=file.txt")).json()
    assert read["content"] == "source"
    changed = await client.put(f"{route}/file", json={"path": "file.txt", "content": "in copy", "expected_sha256": read["sha256"]})
    assert changed.status_code == 200, changed.text
    assert (root / "file.txt").read_text() == "source"
    assert (await client.get(f"{original}/file?path=file.txt")).json()["content"] == "source"
    assert (await client.get(f"{route}/file?path=file.txt")).json()["content"] == "in copy"
    status = (await client.get(f"{route}/git")).json()
    assert status["branch"] == created["workspace"]["branch"]
    committed = await client.post(f"{route}/git/commit", json={"message": "copy only"})
    assert committed.status_code == 200, committed.text
    assert (await gitops.status(str(root)))["initialized"] is False
    assert (await client.post(f"{route}/git/push", json={"branch": "main"})).status_code == 409
    second = (await client.post("/api/chats", json={"domain": "code", "project_id": space.runtime.project_id, "model": "m"})).json()
    # Same-account, same-project chats do not implicitly inherit one another's workspace target.
    assert (await client.get(f"/api/chats/{second['id']}/workspaces/{ident}/archive")).status_code == 404
    assert (await client.get(f"/api/projects/{space.runtime.project_id}/chats/{second['id']}/workspaces/{ident}/file?path=file.txt")).status_code == 404
    await client.post("/api/auth/logout")
    await client.post("/api/auth/register", json={"email": "workspace-other@example.com", "password": "hunter2hunter2"})
    assert (await client.get(f"{route}/file?path=file.txt")).status_code == 404
    assert (await client.post(f"{route}/git/commit", json={"message": "denied"})).status_code == 404


async def test_import_export_archive_and_fork_isolate_files(client, db_sessionmaker):
    space, _manager, chat, root = await controller(client, db_sessionmaker)
    original_log = [
        {"uuid": "a", "parentUuid": None, "type": "user", "message": {"role": "user", "content": "build a file"}},
        {"uuid": "b", "parentUuid": "a", "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "I will write a file."},
            {"type": "tool_use", "id": "tool-1", "name": "write_file", "input": {"path": "a.txt", "content": "hello"}}]}},
        {"uuid": "c", "parentUuid": "b", "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tool-1", "content": "created"}]}},
        {"uuid": "side", "parentUuid": "a", "isSidechain": True, "type": "assistant", "message": {"role": "assistant", "content": "ignore"}},
    ]
    endpoint = f"/api/chats/{chat['id']}/session"
    response = await client.post(endpoint + "/import", json={"text": "\n".join(map(json.dumps, original_log)), "title": "Imported"})
    assert response.status_code == 200, response.text
    imported = response.json()
    transcript = (await client.get(f"/api/chats/{imported['id']}/session/export")).json()["messages"]
    assert len(transcript) == 3 and transcript[1]["tool_calls"][0]["function"]["name"] == "write_file"
    assert transcript[2]["role"] == "tool" and "ignore" not in str(transcript)
    raw = (await client.get(f"/api/chats/{imported['id']}/session/export?original=true")).json()["entries"]
    assert raw == original_log
    (root / "note.txt").write_text("source")
    await space.enter(code_workspace.Enter(name="original"))
    Path((await space.current())[0], "note.txt").write_text("private")
    fork = await client.post(endpoint + "/fork", json={"title": "Fork"})
    assert fork.status_code == 200, fork.text
    target = fork.json()
    copies = (await client.get(f"/api/chats/{target['id']}/workspaces")).json()["workspaces"]
    assert len(copies) == 1 and copies[0]["active"]
    assert Path(copies[0]["path"], "note.txt").read_text() == "private"
    Path(copies[0]["path"], "note.txt").write_text("forked")
    assert Path((await space.current())[0], "note.txt").read_text() == "private"
    assert (root / "note.txt").read_text() == "source"
    assert (await client.get(f"/api/chats/{target['id']}/session/export?original=true")).status_code == 404


def test_import_rejects_duplicate_or_dangling_protocol():
    with pytest.raises(ValueError, match="Повторяющиеся"):
        code_session_transfer.normalize(json.dumps([{"uuid": "one", "type": "user", "content": "a"},
                                                     {"uuid": "one", "type": "user", "content": "b"}]))
    with pytest.raises(ValueError, match="Результат без вызова"):
        code_session_transfer.normalize(json.dumps([{"role": "tool", "tool_call_id": "none", "content": "unexpected"}]))


async def test_failed_fork_removes_partial_chat_and_workspace(client, db_sessionmaker, monkeypatch):
    _space, _manager, chat, _root = await controller(client, db_sessionmaker)
    before = (await client.get("/api/chats?domain=code")).json()
    def fail(_root, _copy):
        raise OSError("snapshot failed")
    monkeypatch.setattr(code_workspace, "_copy_snapshot", fail)
    response = await client.post(f"/api/chats/{chat['id']}/session/fork", json={"title": "broken"})
    assert response.status_code == 409
    after = (await client.get("/api/chats?domain=code")).json()
    assert {row["id"] for row in after} == {row["id"] for row in before}
