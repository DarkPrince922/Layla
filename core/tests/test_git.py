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
        if args and args[0] == "ls-remote":
            return 0, "", ""
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


async def test_agent_commits_and_auto_push_needs_no_extra_approval(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n")
    seen = []
    _script(monkeypatch, [[{"name": "git_commit", "args": {"message": "Добавил a.py"}}],
                          [{"name": "git_push", "args": {}}]], seen)
    asked = []

    async def approve(pending):
        asked.append(pending["name"])
        return "reject"

    async def fake_push(*args, **kwargs):
        return {"branch": "main", "output": "published"}

    monkeypatch.setattr(gitops, "push", fake_push)
    events = await _agent(tmp_path, approve=approve)  # режим «Авто»
    cards = [e["tool"] for e in events if "tool" in e]
    commit = next(c for c in cards if c["name"] == "git_commit" and c["status"] == "done")
    assert "Добавил a.py" in commit["output"]
    assert asked == []
    assert cards[-1]["name"] == "git_push" and cards[-1]["status"] == "done"
    assert (await gitops.log(str(tmp_path)))[0]["message"] == "Добавил a.py"
    assert "git_sync" in seen[0]["conversation"][0]["content"]


async def test_git_tools_follow_modes_and_permissions(tmp_path, monkeypatch):
    seen = []
    _script(monkeypatch, [], seen)
    await _agent(tmp_path, mode="plan")
    assert seen[-1]["tools"] & agent_git.GIT_TOOLS == {"git_status", "git_log", "git_diff", "git_conflicts"}
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


async def test_http_upload_commit_and_server_push(client, monkeypatch):
    project = await _setup(client, 'http-git@example.com')
    pid = project['id']
    assert (await client.put(f'/api/projects/{pid}/upload?path=src/main.py', content=b'print(42)\n')).status_code == 200
    await client.put(f'/api/projects/{pid}/git/remote', json={'url': 'https://github.com/u/r.git'})
    await client.put('/api/git/credentials', json={'host': 'github.com', 'token': 'test_TOKEN'})
    committed = await client.post(f'/api/projects/{pid}/git/commit', json={'message': 'Upload via HTTP'})
    assert committed.status_code == 200
    calls = []
    real = gitops._git

    async def fake(root, *args, **kwargs):
        if args and args[0] == 'ls-remote':
            return 0, '', ''
        if args and args[0] == 'push':
            calls.append((args, kwargs))
            return 0, 'ok', ''
        return await real(root, *args, **kwargs)

    monkeypatch.setattr(gitops, '_git', fake)
    pushed = await client.post(f'/api/projects/{pid}/git/push', json={})
    assert pushed.status_code == 200
    assert calls[0][1]['secret'] == 'test_TOKEN'
    assert list(calls[0][1]['config']) == ['http.https://github.com/.extraheader']
    assert (await client.get(f'/api/projects/{pid}/git')).json()['changes'] == []


async def _diverged_repositories(tmp_path, monkeypatch, conflict=False):
    bare, local, other = (tmp_path / n for n in ('origin.git', 'local', 'other'))
    for directory in (bare, local, other):
        directory.mkdir()
    original = gitops._git

    async def local_transport(root, *args, config=None, **kw):
        # Only test fixtures permit file transport; production keeps it disabled.
        return await original(root, *args, config={**(config or {}), 'protocol.file.allow': 'always'}, **kw)

    monkeypatch.setattr(gitops, '_git', local_transport)
    await gitops._git(str(bare), 'init', '--bare', '-q', '-b', 'main')
    await gitops.init(str(local))
    (local / 'app.txt').write_text('base\n')
    await gitops.commit(str(local), 'base', 'test', 'test@example.com')
    await gitops._git(str(local), 'remote', 'add', 'origin', str(bare))
    await gitops.push(str(local), None)
    await gitops._git(str(tmp_path), 'clone', '-q', str(bare), str(other))
    (local / ('app.txt' if conflict else 'local.txt')).write_text('local work\n')
    await gitops.commit(str(local), 'local work', 'test', 'test@example.com')
    (other / ('app.txt' if conflict else 'remote.txt')).write_text('remote work\n')
    await gitops.commit(str(other), 'remote work', 'test', 'test@example.com')
    await gitops.push(str(other), None)
    return local, bare


async def test_push_automatically_merges_diverged_history_without_force(tmp_path, monkeypatch):
    local, bare = await _diverged_repositories(tmp_path, monkeypatch)
    local_head = (await gitops.log(str(local), 1))[0]['sha']
    await gitops.push(str(local), None)
    assert (local / 'local.txt').read_text() == 'local work\n'
    assert (local / 'remote.txt').read_text() == 'remote work\n'
    assert (await gitops._git(str(bare), 'show', 'main:local.txt'))[1] == 'local work\n'
    assert (await gitops._git(str(bare), 'show', 'main:remote.txt'))[1] == 'remote work\n'
    assert (await gitops._git(str(local), 'merge-base', '--is-ancestor', local_head, 'HEAD'))[0] == 0
    backups = (await gitops._git(str(local), 'for-each-ref', '--format=%(refname)', 'refs/heads/layla/sync-backup/'))[1]
    assert backups.strip()
    assert not (await gitops.status(str(local)))['changes']


async def test_conflict_versions_resolution_markers_hash_and_finish(tmp_path, monkeypatch):
    from app.services import files
    local, bare = await _diverged_repositories(tmp_path, monkeypatch, conflict=True)
    result = await gitops.synchronize(str(local), None)
    assert result['state'] == 'conflicts' and result['conflicts'] == ['app.txt']
    versions = (await gitops.conflicts(str(local)))['conflicts'][0]
    assert versions['base'] == 'base\n'
    assert versions['ours'] == 'local work\n'
    assert versions['theirs'] == 'remote work\n'
    with pytest.raises(gitops.GitError, match='неразрешённые'):
        await gitops.commit(str(local), 'bad', 'test', 'test@example.com')
    current = files.read_file(local, 'app.txt')
    with pytest.raises(gitops.GitError, match='маркеры'):
        await gitops.resolve(str(local), 'app.txt', current['sha256'])
    change = files.change_file(local, 'app.txt', 'local work\nremote work\n', current['sha256'])
    with pytest.raises(gitops.GitError, match='SHA-256'):
        await gitops.resolve(str(local), 'app.txt', current['sha256'])
    assert (await gitops.resolve(str(local), 'app.txt', change['after_sha256']))['remaining'] == []
    with pytest.raises(gitops.GitError, match='git_conflicts'):
        await gitops.push(str(local), None)
    await gitops.commit(str(local), 'Resolve conflict preserving both changes', 'test', 'test@example.com')
    await gitops.push(str(local), None)
    assert (await gitops._git(str(bare), 'show', 'main:app.txt'))[1] == 'local work\nremote work\n'
    assert not (await gitops.status(str(local)))['merging']


async def test_sync_refuses_uncommitted_changes_and_preserves_them(tmp_path, monkeypatch):
    local, _ = await _diverged_repositories(tmp_path, monkeypatch)
    (local / 'draft.txt').write_text('unsaved work\n')
    before = (await gitops.log(str(local), 1))[0]['sha']
    with pytest.raises(gitops.GitError, match='git_commit'):
        await gitops.synchronize(str(local), None)
    assert (local / 'draft.txt').read_text() == 'unsaved work\n'
    assert (await gitops.log(str(local), 1))[0]['sha'] == before


async def test_confirm_mode_still_asks_before_push(tmp_path, monkeypatch):
    await gitops.init(str(tmp_path))
    (tmp_path / 'app.txt').write_text('work')
    await gitops.commit(str(tmp_path), 'work', 'test', 'test@example.com')
    _script(monkeypatch, [[{'name': 'git_push', 'args': {}}]])
    asked = []

    async def approve(pending):
        asked.append(pending['name'])
        return 'reject'

    events = await _agent(tmp_path, mode='confirm', approve=approve)
    assert asked == ['git_push']
    assert any(e.get('tool', {}).get('status') == 'rejected' for e in events)


async def test_agent_repairs_conflict_commits_and_pushes_in_auto(tmp_path, monkeypatch):
    import hashlib
    from app.services import files
    local, bare = await _diverged_repositories(tmp_path, monkeypatch, conflict=True)
    merged = 'local work\nremote work\n'
    calls = []
    step = 0

    async def stream(provider, key, model, conversation, available, **kw):
        nonlocal step
        sequence = ['git_sync', 'git_conflicts', 'read_file', 'write_file', 'git_resolve', 'git_commit', 'git_push']
        if step >= len(sequence):
            yield ('content', 'Объединено и отправлено')
            return
        name = sequence[step]
        args = {}
        if name == 'read_file':
            args = {'path': 'app.txt'}
        elif name == 'write_file':
            args = {'path': 'app.txt', 'content': merged, 'expected_sha256': files.read_file(local, 'app.txt')['sha256']}
        elif name == 'git_resolve':
            args = {'path': 'app.txt', 'expected_sha256': hashlib.sha256(merged.encode()).hexdigest()}
        elif name == 'git_commit':
            args = {'message': 'Combine local and remote work'}
        assert name in {t['function']['name'] for t in available}
        calls.append(name)
        step += 1
        yield ('tool_calls', [{'id': f'call{step}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}])

    monkeypatch.setattr(project_agent.tool_chat, 'stream_turn', stream)
    asked = []

    async def approve(pending):
        asked.append(pending['name'])
        return 'reject'

    events = await _agent(local, mode='auto', approve=approve)
    failures = [e['tool'] for e in events if e.get('tool', {}).get('status') == 'error']
    assert failures == []
    assert asked == []
    assert calls[-1] == 'git_push'
    assert (await gitops._git(str(bare), 'show', 'main:app.txt'))[1] == merged
