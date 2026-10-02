"""Git в Лейле: статус, коммит, история, дифф, удалённый репозиторий, токены, инструменты агента."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services import agent_git, gitops, project_agent

pytestmark = pytest.mark.asyncio


async def test_init_commit_log_show_and_diff(tmp_path):
    (tmp_path / "app.py").write_text("print(1)\n")
    assert (await gitops.status(str(tmp_path))) == {"initialized": False}
    state = await gitops.init(str(tmp_path))
    assert state["initialized"] and state["branch"] == "main" and state["last_commit"] is None
    assert [c["path"] for c in state["changes"]] == ["app.py"]
    diff = await gitops.diff(str(tmp_path))
    assert "+print(1)" in diff["diff"]  # новый файл виден, индекс не тронут
    assert (await gitops.status(str(tmp_path)))["changes"][0]["code"] == "??"

    done = await gitops.commit(str(tmp_path), "Первый коммит", "Лейла Тест", "t@example.com")
    assert done["message"] == "Первый коммит" and done["author"] == "Лейла Тест" and "app.py" in done["stat"]
    with pytest.raises(gitops.GitError, match="Нечего коммитить"):
        await gitops.commit(str(tmp_path), "пусто", "a", "a@b.c")

    (tmp_path / "app.py").write_text("print(2)\n")
    (tmp_path / "new.txt").write_text("hello\n")
    state = await gitops.status(str(tmp_path))
    assert {c["path"]: c["status"] for c in state["changes"]} == {"app.py": "modified", "new.txt": "new"}
    diff = (await gitops.diff(str(tmp_path)))["diff"]
    assert "-print(1)" in diff and "+print(2)" in diff and "+hello" in diff
    second = await gitops.commit(str(tmp_path), "Второй", "a", "a@b.c")
    history = await gitops.log(str(tmp_path))
    assert [c["message"] for c in history] == ["Второй", "Первый коммит"]
    shown = await gitops.show(str(tmp_path), second["short"])
    assert shown["message"] == "Второй" and "+hello" in shown["diff"]
    with pytest.raises(gitops.GitError):
        await gitops.show(str(tmp_path), "--help")


async def test_hooks_are_never_run(tmp_path):
    await gitops.init(str(tmp_path))
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ntouch HOOK_RAN\n")
    hook.chmod(0o755)
    (tmp_path / "a.txt").write_text("a")
    await gitops.commit(str(tmp_path), "с хуком", "a", "a@b.c")
    assert not (tmp_path / "HOOK_RAN").exists()


async def test_remote_validation_and_token_stays_out_of_arguments(tmp_path, monkeypatch):
    await gitops.init(str(tmp_path))
    for bad in ("http://github.com/u/r.git", "https://user:secret@github.com/u/r.git", "file:///etc", "git@github.com:u/r"):
        with pytest.raises(gitops.GitError):
            await gitops.set_remote(str(tmp_path), bad)
    state = await gitops.set_remote(str(tmp_path), "https://github.com/u/r.git")
    assert state["remote"] == "https://github.com/u/r.git"
    (tmp_path / "a.txt").write_text("a")
    await gitops.commit(str(tmp_path), "c", "a", "a@b.c")

    calls = []
    real = gitops._git

    async def fake(root, *args, config=None, timeout=60, secret=None, check=True):
        if args and args[0] == "push":
            calls.append({"args": args, "config": config, "secret": secret})
            return 0, "", "remote: ok ghp_SECRET\n"
        return await real(root, *args, config=config, timeout=timeout, secret=secret, check=check)

    monkeypatch.setattr(gitops, "_git", fake)
    done = await gitops.push(str(tmp_path), "ghp_SECRET", None)
    call = calls[0]
    assert all("ghp_SECRET" not in a for a in call["args"])
    header = next(iter(call["config"].values()))
    assert header.startswith("Authorization: Basic ") and call["secret"] == "ghp_SECRET"
    assert list(call["config"]) == ["http.https://github.com/.extraheader"]
    assert done["branch"] == "main"
    # Токен в выводе git вычищается самим _git — проверим на настоящем вызове.
    _, out, _ = await real(str(tmp_path), "log", "-1", "--format=tformat:ghp_SECRET", secret="ghp_SECRET")
    assert out.strip() == "***"


async def _setup(client, email="git@example.com"):
    await client.post("/api/auth/register", json={"email": email, "password": "hunter2hunter2"})
    await client.post("/api/providers", json={"name": "M", "kind": "openai_compatible",
                                              "base_url": "http://p/v1", "default_model": "m", "active": True})
    return (await client.post("/api/projects", json={"name": "Сайт"})).json()


async def _root(project_id) -> Path:
    from app.db import get_sessionmaker
    from app.main import app
    from app.models.user import Project

    async with app.dependency_overrides[get_sessionmaker]()() as session:
        return Path((await session.get(Project, project_id)).path)


async def test_git_api_flow_and_credentials(client):
    project = await _setup(client)
    pid = project["id"]
    assert (await client.get(f"/api/projects/{pid}/git")).json() == {
        "initialized": False, "remote_host": None, "has_token": False}
    root = await _root(pid)
    (root / "index.html").write_text("<h1>hi</h1>")
    state = (await client.post(f"/api/projects/{pid}/git/init")).json()
    assert state["initialized"] and state["changes"][0]["path"] == "index.html"
    done = (await client.post(f"/api/projects/{pid}/git/commit", json={"message": "Старт"})).json()
    assert done["commit"]["message"] == "Старт" and done["status"]["changes"] == []
    assert done["commit"]["email"] == "git@example.com"
    log = (await client.get(f"/api/projects/{pid}/git/log")).json()
    assert [c["message"] for c in log] == ["Старт"]
    shown = (await client.get(f"/api/projects/{pid}/git/commits/{log[0]['sha']}")).json()
    assert "+<h1>hi</h1>" in shown["diff"]
    bad = await client.put(f"/api/projects/{pid}/git/remote", json={"url": "https://x:tok@github.com/u/r"})
    assert bad.status_code == 400
    state = (await client.put(f"/api/projects/{pid}/git/remote", json={"url": "https://github.com/u/r.git"})).json()
    assert state["remote_host"] == "github.com" and state["has_token"] is False
    saved = (await client.put("/api/git/credentials", json={"host": "github.com", "token": "ghp_abcdef123456"})).json()
    assert saved == {"host": "github.com", "username": None, "masked": "••••3456"}
    assert (await client.get(f"/api/projects/{pid}/git")).json()["has_token"] is True
    assert "ghp_abcdef" not in (await client.get("/api/git/credentials")).text
    assert (await client.delete("/api/git/credentials/github.com")).status_code == 204
    assert (await client.get(f"/api/projects/{pid}/git")).json()["has_token"] is False
    nothing = await client.post(f"/api/projects/{pid}/git/commit", json={"message": "x"})
    assert nothing.status_code == 400 and "Нечего" in nothing.json()["detail"]


def _script(monkeypatch, turns, seen=None):
    state = {"turn": 0}

    async def stream_turn(provider, key, model, conversation, available, **kw):
        if seen is not None:
            seen.append({"tools": {t["function"]["name"] for t in available}, "conversation": list(conversation)})
        calls = turns[state["turn"]] if state["turn"] < len(turns) else []
        state["turn"] += 1
        if calls:
            yield ("tool_calls", [{"id": f"c{i}", "type": "function",
                                   "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                  for i, c in enumerate(calls)])
        else:
            yield ("content", "Готово")

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream_turn)


async def _agent(root, **kw):
    async def credential(url):
        return None, None

    git = agent_git.GitAgent(str(root), "Автор", "a@example.com", credential)
    return [e async for e in project_agent.run(None, "k", "m", [{"role": "user", "content": "x"}], str(root),
                                                git=git, **kw)]


async def test_agent_commits_and_push_always_needs_approval(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n")
    seen = []
    _script(monkeypatch, [[{"name": "git_commit", "args": {"message": "Добавил a.py"}}],
                          [{"name": "git_push", "args": {}}]], seen)
    asked = []

    async def approve(pending):
        asked.append(pending["name"])
        return "reject"

    events = await _agent(tmp_path, approve=approve)  # режим «Авто»
    cards = [e["tool"] for e in events if "tool" in e]
    commit = next(c for c in cards if c["name"] == "git_commit" and c["status"] == "done")
    assert "Добавил a.py" in commit["output"]
    assert asked == ["git_push"]  # коммит в «Авто» без вопросов, пуш — всегда с вопросом
    assert cards[-1]["name"] == "git_push" and cards[-1]["status"] == "rejected"
    assert (await gitops.log(str(tmp_path)))[0]["message"] == "Добавил a.py"
    assert "git_push only" in seen[0]["conversation"][0]["content"]


async def test_git_tools_follow_modes_and_permissions(tmp_path, monkeypatch):
    seen = []
    _script(monkeypatch, [], seen)
    await _agent(tmp_path, mode="plan")
    assert seen[-1]["tools"] & agent_git.GIT_TOOLS == {"git_status", "git_log", "git_diff"}
    await _agent(tmp_path, permissions=["files.read"])
    assert not seen[-1]["tools"] & agent_git.GIT_TOOLS
    await _agent(tmp_path, permissions=["files.read", "repo.git"])
    assert agent_git.GIT_TOOLS <= seen[-1]["tools"]

    _script(monkeypatch, [[{"name": "git_commit", "args": {"message": "m"}}]], seen)
    (tmp_path / "b.txt").write_text("b")
    asked = []

    async def approve(pending):
        asked.append(pending["command"])
        return "approve"

    await _agent(tmp_path, mode="confirm", approve=approve)
    assert asked == ["git commit — m"]


async def test_chat_worker_runs_git_tools(client, monkeypatch):
    project = await _setup(client, "gitchat@example.com")
    _script(monkeypatch, [[{"name": "git_status", "args": {}}]])
    chat = (await client.post("/api/chats", json={"domain": "code", "model": "m", "project_id": project["id"]})).json()
    job = (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "статус", "mode": "auto"})).json()
    for _ in range(300):
        await asyncio.sleep(0.02)
        body = (await client.get(f"/api/jobs/{job['id']}")).json()
        if body["status"] not in ("queued", "running"):
            break
    assert body["status"] == "done", body
    card = (await client.get(f"/api/chats/{chat['id']}")).json()["messages"][-1]["meta"]["tools"][0]
    assert card["name"] == "git_status" and card["output"] == "Проект не под Git"
