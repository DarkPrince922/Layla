"""Skill metadata, real hook scripts in copied workspaces, and agent lifecycle."""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

import pytest

from app.models.user import Project
from app.services import (
    code_hooks,
    code_runner,
    code_skills,
    code_state,
    code_workers,
    project_agent,
    sandbox,
)


def write(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def hooks(root, event, command, **extra):
    write(root, ".layla/hooks.json", json.dumps({"hooks": {event: [{"matcher": "*", "hooks": [{"type": "command", "command": command, **extra}]}]}}))


def call(name, **arguments):
    return {"id": name, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def copied_sandbox(monkeypatch, tmp_path):
    """Test transport: execute benign fixture scripts in a separate copy.

    Production uid/container isolation is exercised by sandbox's own suite; this
    adapter verifies real stdin/stdout/exit behavior without a running service.
    """
    directory = tmp_path.parent / (tmp_path.name + "-sandbox-copy")
    requests = []

    async def run(owner, project, root, command, *, timeout, stdin=None):
        copy = directory / project
        await asyncio.to_thread(shutil.copytree, root, copy, dirs_exist_ok=True)
        assert str(copy) != str(root)
        data = json.loads(stdin)
        requests.append({"owner": owner, "project": project, "root": root, "command": command, "input": data})
        process = await asyncio.create_subprocess_exec("/bin/bash", "-c", command, cwd=copy,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
        try:
            output, _ = await asyncio.wait_for(process.communicate((stdin or "").encode()), timeout)
            yield {"type": "output", "data": output.decode()}
            yield {"type": "exit", "code": process.returncode}
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    monkeypatch.setattr(sandbox, "run", run)
    return requests, directory


def test_skills_frontmatter_precedence_flags_and_literal_arguments(tmp_path):
    write(tmp_path, ".claude/skills/release/SKILL.md", "Lower priority")
    write(tmp_path, ".layla/skills/release/SKILL.md", """---
name: release
description: Prepare a release
argument-hint: '[version] [target]'
arguments: [version, target]
allowed-tools: [Read, Grep]
disable-model-invocation: true
---
$version $target | $ARGUMENTS[0] | $1 | $ARGUMENTS | ${CLAUDE_SKILL_DIR} | ${LAYLA_SESSION_ID}
""")
    catalog = code_skills.Catalog(str(tmp_path))
    skill = catalog.get("release")
    with pytest.raises(ValueError, match="отключён"):
        skill.render("1 prod", "session")
    rendered = skill.render('"$(touch forbidden)" "$ARGUMENTS"', "session", user=True)
    assert '$(touch forbidden) $ARGUMENTS' in rendered
    assert ".layla/skills/release" in rendered and "session" in rendered
    assert not (tmp_path / "forbidden").exists()
    assert skill.allowed == {"read_file", "grep_files"}
    assert skill.public()["model_invocable"] is False


@pytest.mark.parametrize("header", ["x: &x [*x]", "user-invocable: true\nuser-invocable: false", "[a, b]", "!!python/object:os.system {}"])
def test_invalid_yaml_is_reported_without_fallback(tmp_path, header):
    write(tmp_path, ".layla/skills/bad.md", "---\n" + header + "\n---\nbody")
    write(tmp_path, ".claude/skills/bad.md", "must not shadow invalid higher-priority skill")
    catalog = code_skills.Catalog(str(tmp_path))
    assert "bad" in catalog.errors and "bad" not in catalog.skills


async def test_pre_post_stop_hooks_execute_in_order_and_keep_file_checkpoints(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    script = '''import json, sys
d=json.load(sys.stdin)
event=d['hook_event_name']
if event=='SessionStart':
 print(json.dumps({'hookSpecificOutput': {'hookEventName':event,'additionalContext':'session-ready'}}))
elif event=='PreToolUse' and d['tool_input'].get('path')=='blocked.txt':
 print(json.dumps({'hookSpecificOutput': {'hookEventName':event,'permissionDecision':'deny','permissionDecisionReason':'protected path'}}))
elif event=='PreToolUse' and d['tool_name']=='write_file':
 print(json.dumps({'hookSpecificOutput': {'hookEventName':event,'updatedInput':dict(d['tool_input'],content='approved')}}))
elif event=='PostToolUse': print('verified-post')
elif event=='Stop' and not d.get('stop_hook_active'):
 print(json.dumps({'decision':'block','reason':'Verify before completion'}))
else: print('{}')
'''
    write(root, ".layla/gate.py", script)
    command = shlex.quote(sys.executable) + " .layla/gate.py"
    write(root, ".layla/hooks.json", json.dumps({"hooks": {
        event: [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]
        for event in code_hooks.EVENTS
    }}))
    requests, _copy = copied_sandbox(monkeypatch, tmp_path)
    turns = 0

    async def stream(_p, _k, _m, conversation, _available, **_kwargs):
        nonlocal turns
        turns += 1
        assert "session-ready" in conversation[0]["content"]
        if turns == 1:
            yield "tool_calls", [call("write_file", path="blocked.txt", content="denied")]
        elif turns == 2:
            assert "protected path" in conversation[-1]["content"]
            yield "tool_calls", [call("write_file", path="good.txt", content="original")]
        elif turns == 3:
            assert "verified-post" in conversation[-1]["content"]
            yield "content", "Premature finish"
        else:
            assert "Verify before completion" in conversation[-1]["content"]
            yield "content", "Verified and finished"

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    runner = code_runner.Runner("owner", "project", str(root))
    events = [event async for event in project_agent.run(object(), "key", "model",
        [{"role": "user", "content": "Implement and verify"}], str(root), workflow=code_hooks.Runtime(runner))]
    assert not (root / "blocked.txt").exists() and (root / "good.txt").read_text() == "approved"
    assert sum("checkpoint" in item for item in events) == 1
    assert any("retract" in event for event in events) and turns == 4
    names = [request["input"]["hook_event_name"] for request in requests]
    assert names == ["SessionStart", "UserPromptSubmit", "PreToolUse", "PreToolUse", "PostToolUse", "Stop", "Stop", "SessionEnd"]
    assert all(request["owner"] == "owner" and request["project"] == "project" for request in requests)


@pytest.mark.parametrize("mode,permissions", [("plan", None), ("review", None), ("auto", ["files.read"])])
async def test_readonly_workflows_do_not_execute_commands(tmp_path, monkeypatch, mode, permissions):
    hooks(tmp_path, "PreToolUse", "must-not-run")

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("command not allowed")
        yield

    monkeypatch.setattr(sandbox, "run", forbidden)
    runtime = code_hooks.Runtime(code_runner.Runner("owner", "project", str(tmp_path)), permissions, mode=mode)
    await runtime.prepare()
    events = [event async for event in runtime.fire("PreToolUse", {"tool_name": "read_file", "tool_input": {}})]
    assert not events[-1]["hook_result"]["blocked"]
    assert "пропущен" in events[0]["tool"]["note"]


async def test_confirm_rejection_once_and_config_snapshot(tmp_path, monkeypatch):
    hooks(tmp_path, "PreToolUse", "echo original", once=True)
    runtime = code_hooks.Runtime(code_runner.Runner("owner", "project", str(tmp_path)), mode="confirm")
    await runtime.prepare()
    hooks(tmp_path, "PreToolUse", "echo replaced")
    requests, _ = copied_sandbox(monkeypatch, tmp_path)

    async def reject(_event):
        return "reject"

    async def approve(_event):
        return "approve"

    denied = [item async for item in runtime.fire("PreToolUse", {"tool_name": "read_file"}, reject)]
    assert denied[-1]["hook_result"]["blocked"] and not requests
    approved = [item async for item in runtime.fire("PreToolUse", {"tool_name": "read_file"}, approve)]
    assert approved[-1]["hook_result"]["context"] == "original"
    assert "replaced" not in requests[0]["command"]
    again = [item async for item in runtime.fire("PreToolUse", {"tool_name": "read_file"}, approve)]
    assert len(requests) == 1 and not again[-1]["hook_result"]["blocked"]


async def test_slash_skill_narrows_tools_and_does_not_grant_writes(tmp_path, monkeypatch):
    write(tmp_path, ".layla/skills/inspect.md", "---\nallowed-tools: [Read]\ndisable-model-invocation: true\n---\nInspect $ARGUMENTS")
    write(tmp_path, "target.txt", "source")
    turns = 0

    async def stream(_p, _k, _m, conversation, available, **_kwargs):
        nonlocal turns
        turns += 1
        assert {item["function"]["name"] for item in available} == {"read_file"}
        if turns == 1:
            assert "Inspect target.txt" in conversation[-1]["content"]
            # A forged call in the same batch still fails server-side policy.
            yield "tool_calls", [call("write_file", path="forbidden.txt", content="no"), call("read_file", path="target.txt")]
        else:
            yield "content", "Inspected source"

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    runtime = code_hooks.Runtime(runner)
    events = [item async for item in project_agent.run(object(), "key", "model",
        [{"role": "user", "content": "/inspect target.txt"}], str(tmp_path), workflow=runtime)]
    assert turns == 2 and not (tmp_path / "forbidden.txt").exists()
    assert any(item.get("tool", {}).get("name") == "write_file" and item["tool"]["status"] == "error" for item in events)
    with pytest.raises(ValueError, match="отключён"):
        runtime.catalog.get("inspect").render("x", "session")


@pytest.mark.parametrize("output", ["{invalid", 'stderr log\n{"decision":"block"}', '{"hookSpecificOutput":{"hookEventName":"Stop","permissionDecision":"allow"}}'])
async def test_bad_pre_hook_response_fails_closed(tmp_path, monkeypatch, output):
    hooks(tmp_path, "PreToolUse", "fixture")

    async def run(*_args, **_kwargs):
        yield {"type": "output", "data": output}
        yield {"type": "exit", "code": 0}

    monkeypatch.setattr(sandbox, "run", run)
    runtime = code_hooks.Runtime(code_runner.Runner("owner", "project", str(tmp_path)))
    await runtime.prepare()
    events = [item async for item in runtime.fire("PreToolUse", {"tool_name": "write_file"})]
    assert events[-1]["hook_result"]["blocked"]


async def test_skill_catalog_api_has_owner_isolation(client, db_sessionmaker):
    await client.post("/api/auth/register", json={"email": "skills@example.com", "password": "hunter2hunter2"})
    project = (await client.post("/api/projects", json={"name": "Skills"})).json()
    async with db_sessionmaker() as session:
        root = Path((await session.get(Project, project["id"])).path)
    write(root, ".layla/skills/review.md", "---\ndescription: Review changes\nuser-invocable: false\n---\nReview code")
    result = await client.get(f"/api/projects/{project['id']}/skills")
    assert result.status_code == 200 and result.json()["skills"][0]["user_invocable"] is False
    await client.post("/api/auth/register", json={"email": "other-skills@example.com", "password": "hunter2hunter2"})
    assert (await client.get(f"/api/projects/{project['id']}/skills")).status_code == 404


@pytest.mark.parametrize("role", ["Explore", "implement"])
async def test_fork_skill_uses_real_worker_and_preserves_tool_scope_on_resume(client, db_sessionmaker, monkeypatch, tmp_path, role):
    owner = (await client.post("/api/auth/register", json={"email": "fork@example.com", "password": "hunter2hunter2"})).json()["id"]
    project = (await client.post("/api/projects", json={"name": "Fork skill"})).json()
    provider = (await client.post("/api/providers", json={"name": "Mock", "kind": "openai_compatible",
        "base_url": "http://mock/v1", "default_model": "m", "active": True})).json()
    chat = (await client.post("/api/chats", json={"domain": "code", "project_id": project["id"], "model": "m"})).json()
    async with db_sessionmaker() as session:
        root = Path((await session.get(Project, project["id"])).path)
    write(root, ".layla/skills/inspect.md", "---\ncontext: fork\nagent: " + role + "\nallowed-tools: [Read]\nhooks:\n  Stop:\n    - hooks:\n        - type: command\n          command: \"printf 'verified-child'\"\n---\nRead the target file and report: $ARGUMENTS")
    write(root, "target.txt", "independent input")
    requests, _ = copied_sandbox(monkeypatch, tmp_path)
    turns = {"lead": 0, "child": 0}

    async def stream(_p, _k, _m, conversation, available, **_kwargs):
        names = {tool["function"]["name"] for tool in available}
        role = "lead" if "agent_start" in names else "child"
        turns[role] += 1
        if role == "child":
            assert names == {"read_file"}
            if turns[role] == 1:
                yield "tool_calls", [call("read_file", path="target.txt")]
            else:
                assert "independent input" in conversation[-1]["content"]
                yield "content", "Inspected independent input."
        else:
            if turns[role] == 1:
                assert "worker" in conversation[-1]["content"]
                yield "content", "Collecting the worker"
            else:
                assert "workers_completed" in conversation[-1]["content"]
                yield "content", "Finished after independent review."

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    started = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "/inspect target.txt"})
    assert started.status_code == 202
    for _ in range(1000):
        job = (await client.get(f"/api/jobs/{started.json()['id']}")).json()
        if job["status"] in {"done", "error"}:
            break
        await asyncio.sleep(0.01)
    assert job["status"] == "done", job
    runtime = code_state.Runtime(db_sessionmaker, owner, chat["id"], project["id"])
    manager = code_workers.Manager(runtime, str(root), provider_id=provider["id"], model="m")
    workers = await manager.list()
    assert len(workers) == 1 and workers[0]["role"] == ("explore" if role == "Explore" else role) and workers[0]["status"] == "done"
    assert turns == {"lead": 2, "child": 2}
    from app.models.code_worker import CodeWorker

    async with db_sessionmaker() as session:
        row = await session.get(CodeWorker, workers[0]["id"])
        assert row.payload["tool_allowlist"] == ["read_file"]
        row.status = "interrupted"
        await session.commit()
    turns["child"] = 0
    manager.tool_allowlist = ["read_file", "write_file"]
    resumed = await manager.resume(workers[0]["id"])
    for _ in range(1000):
        result = await manager.output(code_workers.WorkerOutput(worker_id=resumed["worker"]["id"]))
        if result["worker"]["status"] not in code_workers.ACTIVE:
            break
        await asyncio.sleep(0.01)
    assert result["worker"]["status"] == "done", result
    assert turns["child"] == 2
    assert len(requests) == (2 if role == "implement" else 0)
    assert all(request["project"] == workers[0]["id"] for request in requests)


async def test_failed_tool_feedback_and_hook_cancellation_are_preserved(tmp_path, monkeypatch):
    hooks(tmp_path, "PostToolUseFailure", "fixture")
    requests = []

    async def run(*_args, **kwargs):
        requests.append(json.loads(kwargs["stdin"]))
        yield {"type": "output", "data": "Read a real path before retrying"}
        yield {"type": "exit", "code": 0}

    turns = 0

    async def stream(*_args, **_kwargs):
        nonlocal turns
        turns += 1
        if turns == 1:
            yield "tool_calls", [call("read_file", path="absent.txt")]
        else:
            assert "Read a real path" in _args[3][-1]["content"]
            yield "content", "Missing file reported."

    monkeypatch.setattr(sandbox, "run", run)
    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    _ = [item async for item in project_agent.run(object(), "key", "model", [{"role": "user", "content": "Read"}],
        str(tmp_path), workflow=code_hooks.Runtime(runner))]
    assert requests[0]["hook_event_name"] == "PostToolUseFailure" and "error" in requests[0]["tool_response"]
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def blocking(*_args, **_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        yield

    hooks(tmp_path, "SessionStart", "blocking fixture")
    monkeypatch.setattr(sandbox, "run", blocking)
    workflow = code_hooks.Runtime(runner)
    await workflow.prepare()

    async def consume():
        return [item async for item in workflow.fire("SessionStart", {})]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


async def test_hook_allow_cannot_grant_persona_write_permission(tmp_path, monkeypatch):
    hooks(tmp_path, "PreToolUse", "printf '%s' '{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"allow\"}}'")
    write(tmp_path, "readme.txt", "existing")
    requests, _ = copied_sandbox(monkeypatch, tmp_path)
    turns = 0

    async def stream(*_args, **_kwargs):
        nonlocal turns
        turns += 1
        if turns == 1:
            yield "tool_calls", [call("read_file", path="readme.txt"), call("write_file", path="blocked.txt", content="no")]
        else:
            yield "content", "Read only."

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    events = [item async for item in project_agent.run(object(), "key", "model", [{"role": "user", "content": "Read only"}],
        str(tmp_path), ["files.read", "code.run"], workflow=code_hooks.Runtime(runner))]
    assert not (tmp_path / "blocked.txt").exists() and not any("checkpoint" in item for item in events)
    assert len(requests) == 1 and requests[0]["input"]["tool_name"] == "read_file"


async def test_stop_block_loop_is_bounded(tmp_path, monkeypatch):
    hooks(tmp_path, "Stop", "fixture")
    calls = 0

    async def run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        yield {"type": "output", "data": "Still blocked"}
        yield {"type": "exit", "code": 2}

    async def stream(*_args, **_kwargs):
        yield "content", "Finished"

    monkeypatch.setattr(sandbox, "run", run)
    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    with pytest.raises(RuntimeError, match="повторно"):
        _ = [item async for item in project_agent.run(object(), "key", "model", [{"role": "user", "content": "Finish"}],
            str(tmp_path), workflow=code_hooks.Runtime(runner))]
    assert calls == 3


async def test_disable_all_hooks_and_startup_matcher(tmp_path, monkeypatch):
    command = {"type": "command", "command": "fixture"}
    config = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [command]}]}}
    write(tmp_path, ".layla/hooks.json", json.dumps(config))
    calls = []

    async def run(*_args, **_kwargs):
        calls.append(True)
        yield {"type": "exit", "code": 0}

    monkeypatch.setattr(sandbox, "run", run)
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    workflow = code_hooks.Runtime(runner)
    await workflow.prepare()
    _ = [item async for item in workflow.fire("SessionStart", {"source": "resume"})]
    assert not calls
    _ = [item async for item in workflow.fire("SessionStart", {"source": "startup"})]
    assert len(calls) == 1
    write(tmp_path, ".claude/settings.json", json.dumps({"hooks": {"UnsupportedEvent": [{"hooks": [{"type": "prompt"}]}]}}))
    write(tmp_path, ".layla/hooks.json", json.dumps({**config, "disableAllHooks": True}))
    disabled = code_hooks.Runtime(runner)
    await disabled.prepare()
    _ = [item async for item in disabled.fire("SessionStart", {"source": "startup"})]
    assert len(calls) == 1
    assert disabled.load_error is None
