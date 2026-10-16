"""Rules/skills contracts across discovery, policy and real command transport."""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.user import Project
from app.services import (
    code_hooks,
    code_instructions,
    code_markdown,
    code_patterns,
    code_runner,
    code_skill_policy,
    code_skills,
    project_agent,
    sandbox,
)


def write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def call(tool_name, **arguments):
    return {"id": tool_name, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(arguments)}}


@pytest.mark.parametrize(("path", "expected"), [
    ("src/main.py", True), ("src/lib/a.ts", True), ("test/b.py", True),
    ("src/generated/a.py", False), ("src/a.js", False), (".hidden/a.py", False),
])
def test_braces_extglob_and_negated_pattern_sets(path, expected):
    patterns = code_patterns.split_patterns("{src,test}/**/*.@(py|ts), !src/generated/**")
    assert len(patterns) == 2
    assert code_patterns.matches(path, patterns) is expected
    assert code_patterns.matches(".hidden/a.py", ["**/*.py"])


@pytest.mark.parametrize("value", [["{1..500}"], ["../secret"], ["/etc/**"], ["C:/secret"], ["~/**"]])
def test_pattern_validation_is_bounded_and_relative(value):
    with pytest.raises(ValueError):
        code_patterns.split_patterns(value)


def test_shared_include_has_separate_conditional_bindings_and_cached_text(tmp_path):
    for area in ("api", "web"):
        write(tmp_path, f".claude/rules/{area}.md", f"---\npaths: ['{area}/**']\n---\n@../../shared.md")
    write(tmp_path, "shared.md", "---\npaths: ['**/*.py']\n---\nSHARED_FIRST")
    book = code_instructions.Book(str(tmp_path))
    api = [i for i in book.for_path("api/a.py", fresh=True)["instructions"] if i["path"] == "shared.md"]
    write(tmp_path, "shared.md", "CHANGED")
    web = [i for i in book.for_path("web/b.py", fresh=True)["instructions"] if i["path"] == "shared.md"]
    assert len(api) == len(web) == 1 and api[0]["binding_id"] != web[0]["binding_id"]
    assert api[0]["content"] == web[0]["content"] == "SHARED_FIRST"
    assert not book.for_path("api/a.py", fresh=True)["instructions"]
    assert "SHARED_FIRST" not in json.dumps(book.for_path("web/b.ts"))


def test_exclusions_comments_and_markdown_imports(tmp_path):
    write(tmp_path, ".layla/settings.json", json.dumps({"claudeMdExcludes": ["vendor/**", "skip.md"]}))
    write(tmp_path, "CLAUDE.md", "<!-- @hidden.md -->\n- @docs/real.md#section\n> @docs/quoted.md\n@skip.md\n`@inline.md`\n```\n@fence.md\n```\n")
    for path in ("hidden.md", "inline.md", "fence.md", "skip.md", "vendor/CLAUDE.md", "docs/real.md", "docs/quoted.md"):
        write(tmp_path, path, "CONTENT:" + path)
    result = code_instructions.Book(str(tmp_path)).for_path("vendor/new.py")
    assert {item["path"] for item in result["instructions"]} == {"CLAUDE.md", "docs/real.md", "docs/quoted.md"}
    assert "<!--" not in result["instructions"][0]["content"]
    assert code_instructions.tool_paths("run_code", {"paths": None}) == []
    assert code_instructions.tool_paths("run_command", {"cwd": "src"}) == ["src/.instruction-target"]
    assert list(code_markdown.includes("@docs/my\\ file.md\n\\@ignored.md\n")) == ["docs/my file.md"]
    assert code_markdown.strip_comments("before <!-- hidden --> after\n`<!-- preserved -->`\n") == "before  after\n`<!-- preserved -->`\n"


def test_nested_namespaces_conditional_skills_and_snapshot(tmp_path):
    write(tmp_path, ".claude/skills/check.md", "ROOT")
    write(tmp_path, "src/.layla/skills/check/SKILL.md", "---\nname: Human readable\npaths: ['**/*.py', '!generated/**']\n---\nNESTED")
    write(tmp_path, "src/.claude/commands/ops/deploy.md", "DEPLOY")
    write(tmp_path, "src/.layla/skills/check/helper.md", "SUPPORT")
    catalog = code_skills.Catalog(str(tmp_path))
    assert catalog.get("check").body == "ROOT"
    catalog.discover("src/readme.txt")
    assert catalog.public(model=True)["skills"] == [catalog.get("ops:deploy").public()]
    with pytest.raises(ValueError, match="paths"):
        catalog.get("check")
    assert catalog.get("check", user=True).public()["display_name"] == "Human readable"
    catalog.discover("src/main.py")
    nested = catalog.get("check")
    assert nested.body == "NESTED" and nested.resources(str(tmp_path), "helper.md")["content"] == "SUPPORT"
    write(tmp_path, nested.path, "DISK_CHANGED")
    catalog.discover("src/other.py")
    assert catalog.get("check").body == "NESTED"
    catalog.discover("other/main.py")
    assert catalog.get("check").body == "ROOT" and "ops:deploy" not in catalog.skills


def test_dynamic_sources_and_outputs_are_never_rescanned(tmp_path):
    write(tmp_path, ".layla/skills/context.md", "---\narguments: [target]\n---\n!`printf '%s' \"$target\"`\n\n```!\nprintf done\n```\n\n```example\n!`must-not-run`\n```\n\n<!-- !`hidden` -->\n\n`!\u0060example\u0060`\n$ARGUMENTS\n")
    skill = code_skills.Catalog(str(tmp_path)).get("context")
    commands = skill.dynamic("'$(touch injected)'", "session")
    assert len(commands) == 2 and commands[0]["source"].startswith("printf")
    output = "!`touch output-injected` $ARGUMENTS \x00skill-command-1\x00"
    text = skill.render("!`touch argument-injected`", "session", dynamic=[output, "second"])
    assert output in text and "second" in text and not (tmp_path / "injected").exists()
    assert len(code_markdown.commands("`ordinary !`no` sample`\n\\!`escaped`\n")) == 0


@pytest.mark.parametrize(("tool", "arguments", "expected"), [
    ("read_file", {"path": "src/a.py"}, True), ("read_file", {"path": "secret/a.py"}, False),
    ("run_command", {"command": "git status --short"}, True),
    ("run_command", {"command": "git status; touch bad"}, False),
    ("run_command", {"command": "git status $(touch bad)"}, False),
    ("run_command", {"command": "git status && echo bad"}, False),
    ("run_command", {"command": "git status > bad"}, False),
    ("agent_start", {"role": "explore"}, True), ("agent_start", {"role": "implement"}, False),
])
def test_argument_aware_allowed_tools(tool, arguments, expected):
    rules = code_skill_policy.parse("Read(src/**), Bash(git status:*), Agent(Explore)")
    assert code_skill_policy.allows(rules, tool, arguments) is expected
    assert code_skill_policy.merge([rules], [rules]) == [rules]


async def test_nested_only_skill_remains_discoverable_and_activation_uses_snapshot(tmp_path):
    write(tmp_path, "src/.layla/skills/check.md", "ORIGINAL")
    runtime = code_hooks.Runtime(code_runner.Runner("owner", "project", str(tmp_path)))
    await runtime.prepare()
    assert runtime.tools()[0]["function"]["name"] == "invoke_skill"
    runtime.catalog.discover("src/file.py")
    write(tmp_path, "src/.layla/skills/check.md", "EDITED")
    assert runtime.invoke(code_hooks.Invoke(name="check"))["instructions"] == "ORIGINAL"


async def test_inline_model_effort_and_same_batch_tool_policy(tmp_path, monkeypatch):
    write(tmp_path, ".layla/skills/check.md", "---\nmodel: fast\neffort: high\nallowed-tools: [Read(src/**), Skill]\n---\nInspect source")
    write(tmp_path, ".layla/skills/inherit.md", "---\nallowed-tools: [Read(src/**)]\n---\nKeep inherited model and effort")
    write(tmp_path, "src/a.py", "ALLOWED_SOURCE")
    write(tmp_path, "private.txt", "PRIVATE")
    provider = SimpleNamespace(models=[{"name": "fast"}], default_model="normal", model_caps={"fast": {"context_window": 8000}})
    budget, turns = {}, 0

    async def stream(_p, _k, model, conversation, available, **kwargs):
        nonlocal turns
        turns += 1
        assert model == "fast" and kwargs["caps"]["reasoning_effort"] == "high"
        if turns == 1:
            yield "tool_calls", [call("read_file", path="private.txt"), call("read_file", path="src/a.py")]
        elif turns == 2:
            assert "PRIVATE" not in json.dumps(conversation)
            assert "ALLOWED_SOURCE" in conversation[-1]["content"]
            yield "tool_calls", [call("invoke_skill", name="inherit")]
        else:
            assert budget["skill_effort"] == "high"
            yield "content", "Checked"

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    events = [item async for item in project_agent.run(provider, "key", "normal", [{"role": "user", "content": "/check"}],
        str(tmp_path), workflow=code_hooks.Runtime(runner), worker_context=True, budget=budget)]
    assert budget["skill_model"] == "fast" and budget["skill_effort"] == "high" and turns == 3
    assert any(item.get("tool", {}).get("status") == "error" for item in events)
    with pytest.raises(ValueError, match="включённой"):
        code_skills.execution_model(provider, "normal", {}, "foreign")
    provider.models[0]["enabled"] = False
    with pytest.raises(ValueError):
        code_skills.execution_model(provider, "normal", {}, "fast")


async def command_transport(monkeypatch, tmp_path):
    """Real bash in a copy; hook stdin and ordinary runner use the same transport."""
    requests = []
    async def info():
        return {"shells": ["bash"]}
    async def runtimes():
        return []
    async def run(owner, project, root, command, *, timeout, stdin=None):
        copy = tmp_path.parent / (tmp_path.name + "-commands")
        shutil.copytree(root, copy, dirs_exist_ok=True)
        requests.append({"command": command, "input": json.loads(stdin) if stdin is not None else None})
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
    monkeypatch.setattr(sandbox, "info", info)
    monkeypatch.setattr(sandbox, "runtimes", runtimes)
    monkeypatch.setattr(sandbox, "run", run)
    return requests


async def test_dynamic_commands_use_real_runner_hooks_and_literal_environment(tmp_path, monkeypatch):
    write(tmp_path, ".layla/skills/context.md", '---\narguments: [target]\nallowed-tools: [Bash(printf:*), Read]\n---\n!`printf \'%s\' "$target"`')
    write(tmp_path, ".layla/hooks.json", json.dumps({"hooks": {event: [{"matcher": "run_command", "hooks": [
        {"type": "command", "command": "printf hook-feedback"}]}] for event in ("PreToolUse", "PostToolUse")}}))
    requests = await command_transport(monkeypatch, tmp_path)
    turns = 0
    async def stream(*args, **kwargs):
        nonlocal turns
        turns += 1
        assert "$(touch injected)" in args[3][-1]["content"] and "hook-feedback" in args[3][-1]["content"]
        yield "content", "Context collected"
    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    events = [item async for item in project_agent.run(object(), "key", "m", [{"role": "user", "content": "/context '$(touch injected)'"}],
        str(tmp_path), runner=runner, workflow=code_hooks.Runtime(runner), worker_context=True)]
    assert turns == 1 and [item["input"]["hook_event_name"] for item in requests if item["input"]] == ["PreToolUse", "PostToolUse"]
    assert len([item for item in requests if item["input"] is None]) == 1
    assert not (tmp_path.parent / (tmp_path.name + "-commands") / "injected").exists()
    assert any(item.get("tool", {}).get("name") == "run_command" and item["tool"]["status"] == "done" for item in events)


@pytest.mark.parametrize("mode", ["plan", "review", "confirm"])
async def test_dynamic_commands_respect_mode_and_confirmation(tmp_path, monkeypatch, mode):
    write(tmp_path, ".layla/skills/context.md", "!`printf once`")
    requests = await command_transport(monkeypatch, tmp_path)
    async def stream(*args, **kwargs):
        assert '"error"' in args[3][-1]["content"]
        yield "content", "Execution denied"
    async def reject(_event):
        return "reject"
    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    _events = [item async for item in project_agent.run(object(), "key", "m", [{"role": "user", "content": "/context"}],
        str(tmp_path), mode=mode, approve=reject, runner=runner, workflow=code_hooks.Runtime(runner), worker_context=True)]
    assert not requests


async def test_failed_dynamic_command_is_not_replayed(tmp_path, monkeypatch):
    write(tmp_path, ".layla/skills/context.md", "!`printf ran; exit 7`")
    requests = await command_transport(monkeypatch, tmp_path)
    turns = 0
    async def stream(*args, **kwargs):
        nonlocal turns
        turns += 1
        assert '"error"' in args[3][-1]["content"]
        if turns == 1:
            yield "tool_calls", [call("invoke_skill", name="context")]
        else:
            assert "уже запускались" in args[3][-1]["content"]
            yield "content", "Failure reported"
    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    _events = [item async for item in project_agent.run(object(), "key", "m", [{"role": "user", "content": "/context"}],
        str(tmp_path), runner=runner, workflow=code_hooks.Runtime(runner), worker_context=True)]
    assert len(requests) == 1


async def test_dynamic_hook_retarget_cannot_expand_skill_policy(tmp_path, monkeypatch):
    write(tmp_path, ".layla/skills/context.md", "---\nallowed-tools: [Bash(printf:*)]\n---\n!`printf allowed`")
    decision = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"command": "touch forbidden"}}})
    write(tmp_path, ".layla/hooks.json", json.dumps({"hooks": {"PreToolUse": [{"matcher": "run_command", "hooks": [
        {"type": "command", "command": "printf '%s' " + shlex.quote(decision)}]}]}}))
    requests = await command_transport(monkeypatch, tmp_path)
    async def stream(*args, **kwargs):
        assert "retarget violates" in args[3][-1]["content"]
        yield "content", "Retarget denied"
    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    _events = [item async for item in project_agent.run(object(), "key", "m", [{"role": "user", "content": "/context"}],
        str(tmp_path), runner=runner, workflow=code_hooks.Runtime(runner), worker_context=True)]
    assert len(requests) == 1 and requests[0]["input"]["hook_event_name"] == "PreToolUse"


async def test_unknown_model_rejected_before_dynamic_context(tmp_path, monkeypatch):
    write(tmp_path, ".layla/skills/context.md", "---\nmodel: unavailable\n---\n!`printf should-not-run`")
    requests = await command_transport(monkeypatch, tmp_path)
    async def stream(*args, **kwargs):
        assert "включённой моделью" in args[3][-1]["content"]
        yield "content", "Model unavailable"
    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    provider = SimpleNamespace(default_model="m", models=[])
    _events = [item async for item in project_agent.run(provider, "key", "m", [{"role": "user", "content": "/context"}],
        str(tmp_path), runner=runner, workflow=code_hooks.Runtime(runner), worker_context=True)]
    assert not requests


async def test_catalog_api_path_and_slash_path_follow_nested_project(client, db_sessionmaker, monkeypatch):
    await client.post("/api/auth/register", json={"email": "nested-skills@example.com", "password": "hunter2hunter2"})
    project = (await client.post("/api/projects", json={"name": "Nested skills"})).json()
    await client.post("/api/providers", json={"name": "Mock", "kind": "openai_compatible", "base_url": "http://mock/v1", "default_model": "m", "active": True})
    chat = (await client.post("/api/chats", json={"domain": "code", "project_id": project["id"], "model": "m"})).json()
    async with db_sessionmaker() as session:
        root = Path((await session.get(Project, project["id"])).path)
    write(root, "src/.layla/skills/check.md", "NESTED_SELECTED")
    assert not [skill for skill in (await client.get(f"/api/projects/{project['id']}/skills")).json()["skills"] if skill["source"] == "project"]
    result = await client.get(f"/api/projects/{project['id']}/skills", params={"path": "src"})
    assert result.status_code == 200 and result.json()["skills"][0]["base"] == "src"
    assert (await client.get(f"/api/projects/{project['id']}/skills", params={"path": "../outside"})).status_code == 422
    async def stream(*args, **kwargs):
        assert "NESTED_SELECTED" in args[3][-1]["content"]
        yield "content", "Nested skill done"
    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    invalid = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "/check", "skill_path": "../outside"})
    assert invalid.status_code == 422
    started = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "/check", "skill_path": "src"})
    assert started.status_code == 202
    for _ in range(500):
        job = (await client.get(f"/api/jobs/{started.json()['id']}")).json()
        if job["status"] in {"done", "error"}:
            break
        await asyncio.sleep(0.01)
    assert job["status"] == "done", job
