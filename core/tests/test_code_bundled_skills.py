"""Packaged skills, project precedence and real Code run/worker integration."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.user import Project
from app.services import (
    code_hooks,
    code_runner,
    code_skills,
    code_state,
    code_workers,
    files,
    project_agent,
)


def write(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def call(tool_name, **arguments):
    return {"id": uuid4().hex, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(arguments)}}


def test_packaged_catalog_metadata_template_and_model_visibility(tmp_path):
    assert not code_skills.Catalog(str(tmp_path)).skills  # non-Code callers remain project-only
    catalog = code_skills.Catalog(str(tmp_path), bundled=True)
    assert set(catalog.skills) == {"simplify", "skillify", "remember"}
    assert [item["name"] for item in catalog.public(model=True)["skills"]] == ["simplify"]
    assert all(item["source"] == "bundled" for item in catalog.public()["skills"])
    assert all(item["user_invocable"] for item in catalog.public()["skills"])
    assert all("content" not in item for item in catalog.public()["skills"])
    skill = catalog.get("skillify")
    with pytest.raises(ValueError, match="отключён"):
        skill.render("", "session")
    rendered = skill.render("capture !`do-not-execute`", "session", user=True)
    assert "Layla /skillify" in rendered and "capture !`do-not-execute`" in rendered
    assert not skill.dynamic("capture !`do-not-execute`", "session")
    template = skill.resources(str(tmp_path), "templates/SKILL.md")
    meta, body = code_skills.frontmatter(template["content"])
    assert meta["arguments"] == ["target"] and "$target" in body
    assert template["path"].startswith("bundled://layla/skillify/")
    assert skill.resources(str(tmp_path), "SKILL.md")["content"] == skill.raw
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name", ["simplify", "skillify", "remember"])
def test_root_nested_and_invalid_project_override_bundled(tmp_path, name):
    write(tmp_path, f"src/.layla/skills/{name}.md", "NESTED_OVERRIDE")
    catalog = code_skills.Catalog(str(tmp_path), bundled=True)
    assert catalog.get(name, user=True).source == "bundled"
    catalog.discover("src/new.py")
    assert catalog.get(name, user=True).body == "NESTED_OVERRIDE"
    catalog.discover("other/new.py")
    assert catalog.get(name, user=True).source == "bundled"
    write(tmp_path, f".layla/skills/{name}.md", "---\nallowed-tools: invalid(\n---\nBAD")
    invalid = code_skills.Catalog(str(tmp_path), bundled=True)
    assert name in invalid.errors and name not in invalid.skills
    with pytest.raises(ValueError):
        invalid.get(name, user=True)
    write(tmp_path, f".layla/skills/{name}.md", "ROOT_OVERRIDE")
    assert code_skills.Catalog(str(tmp_path), bundled=True).get(name, user=True).body == "ROOT_OVERRIDE"


@pytest.mark.parametrize("path", ["../remember/SKILL.md", "/etc/passwd", "missing.md", "templates/other.md"])
def test_bundled_resources_cannot_read_project_or_external_files(tmp_path, path):
    write(tmp_path, "missing.md", "PROJECT_SECRET")
    skill = code_skills.Catalog(str(tmp_path), bundled=True).get("skillify", user=True)
    with pytest.raises(ValueError):
        skill.resources(str(tmp_path), path)


async def test_skillify_reads_template_and_only_writes_project_skills(tmp_path, monkeypatch):
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    workflow = code_hooks.Runtime(runner)
    target = ".layla/skills/check/SKILL.md"
    document = "---\ndescription: Check source\narguments: [target]\n---\nRead $target and run the appropriate checks."
    turns = 0

    async def stream(_p, _k, _m, conversation, available, **_kwargs):
        nonlocal turns
        turns += 1
        names = {item["function"]["name"] for item in available}
        assert "write_file" in names and "run_command" not in names and "agent_start" not in names
        if turns == 1:
            assert "Layla /skillify" in conversation[-1]["content"]
            yield "tool_calls", [call("read_skill", name="skillify", file="templates/SKILL.md")]
        elif turns == 2:
            assert "Критерии готовности" in conversation[-1]["content"]
            yield "tool_calls", [call("write_file", path=target, content=document),
                                 call("write_file", path="application.py", content="FORBIDDEN")]
        elif turns == 3:
            assert not conversation[-2].get("is_error"), conversation[-2]
            assert "allowed-tools" in conversation[-1]["content"]
            yield "tool_calls", [call("read_file", path=target)]
        else:
            assert document in json.loads(conversation[-1]["content"])["content"]
            yield "content", "Saved /check; available on the next request."

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    events = [item async for item in project_agent.run(object(), "key", "m",
        [{"role": "user", "content": "/skillify capture the source verification workflow"}], str(tmp_path),
        workflow=workflow, worker_context=True)]
    assert (tmp_path / target).read_text() == document and not (tmp_path / "application.py").exists()
    assert sum("checkpoint" in item for item in events) == 1
    assert "check" not in workflow.catalog.skills
    assert code_skills.Catalog(str(tmp_path), bundled=True).get("check").body.endswith("checks.")


@pytest.mark.parametrize(("skill", "mode", "permissions"), [
    ("skillify", "plan", None), ("skillify", "auto", ["files.read"]), ("remember", "auto", None),
])
async def test_bundled_skills_respect_readonly_modes_and_persona(tmp_path, monkeypatch, skill, mode, permissions):
    runner = code_runner.Runner("owner", "project", str(tmp_path))
    turns = 0

    async def stream(_p, _k, _m, conversation, available, **_kwargs):
        nonlocal turns
        turns += 1
        assert "write_file" not in {item["function"]["name"] for item in available}
        if turns == 1:
            yield "tool_calls", [call("write_file", path=".layla/skills/new/SKILL.md", content="FORBIDDEN")]
        else:
            assert '"error"' in conversation[-1]["content"]
            yield "content", "Draft only"

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    _events = [item async for item in project_agent.run(object(), "key", "m", [{"role": "user", "content": "/" + skill}],
        str(tmp_path), permissions, mode=mode, workflow=code_hooks.Runtime(runner), worker_context=True)]
    assert not (tmp_path / ".layla/skills/new/SKILL.md").exists()


async def setup(client, maker):
    owner = (await client.post("/api/auth/register", json={"email": "bundled@example.com", "password": "hunter2hunter2"})).json()["id"]
    project = (await client.post("/api/projects", json={"name": "Bundled skills"})).json()
    provider = (await client.post("/api/providers", json={"name": "Mock", "kind": "openai_compatible",
        "base_url": "http://mock/v1", "default_model": "m", "active": True})).json()
    chat = (await client.post("/api/chats", json={"domain": "code", "project_id": project["id"], "model": "m"})).json()
    async with maker() as session:
        root = Path((await session.get(Project, project["id"])).path)
    runtime = code_state.Runtime(maker, owner, chat["id"], project["id"])
    return project, chat, provider, root, runtime


async def finish(client, job_id):
    for _ in range(1000):
        result = (await client.get(f"/api/jobs/{job_id}")).json()
        if result["status"] in {"done", "error"}:
            assert result["status"] == "done", result
            return result
        await asyncio.sleep(0.01)
    raise AssertionError("Code job did not finish")


async def test_remember_api_catalog_and_review_cannot_modify_memory_or_rules(client, db_sessionmaker, monkeypatch):
    project, chat, _provider, root, runtime = await setup(client, db_sessionmaker)
    write(root, "LAYLA.md", "Use pytest for checks.")
    await runtime.call("memory_write", {"key": "checks", "content": "Use pytest for checks."})
    response = await client.get(f"/api/projects/{project['id']}/skills")
    skills = {item["name"]: item for item in response.json()["skills"]}
    assert response.status_code == 200 and set(skills) == {"simplify", "skillify", "remember"}
    assert all(item["source"] == "bundled" for item in skills.values())
    turns = 0

    async def stream(_p, _k, _m, conversation, available, **_kwargs):
        nonlocal turns
        turns += 1
        names = {item["function"]["name"] for item in available}
        assert "memory_search" in names and not {"memory_write", "memory_delete", "write_file", "run_command"} & names
        if turns == 1:
            assert "Layla /remember" in conversation[-1]["content"]
            yield "tool_calls", [call("memory_search")]
        elif turns == 2:
            assert "Use pytest" in conversation[-1]["content"]
            yield "tool_calls", [call("memory_delete", key="checks"), call("write_file", path="LAYLA.md", content="FORBIDDEN")]
        else:
            yield "content", "Propose removing the duplicate checks note after approval. Nothing changed."

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    started = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "/remember"})
    assert started.status_code == 202
    await finish(client, started.json()["id"])
    assert (root / "LAYLA.md").read_text() == "Use pytest for checks."
    assert (await runtime.call("memory_search", {}))["memory"][0]["key"] == "checks"
    await client.post("/api/auth/register", json={"email": "other-bundled@example.com", "password": "hunter2hunter2"})
    assert (await client.get(f"/api/projects/{project['id']}/skills")).status_code == 404


async def test_simplify_uses_three_readonly_workers_then_checkpointed_lead_fix(client, db_sessionmaker, monkeypatch):
    _project, chat, provider, root, runtime = await setup(client, db_sessionmaker)
    write(root, "calc.py", "VALUE = (1 + 1)\n")
    original = files.read_file(str(root), "calc.py")
    lead_turns, child_tasks = 0, []

    async def stream(_p, _k, _m, conversation, available, **_kwargs):
        nonlocal lead_turns
        names = {item["function"]["name"] for item in available}
        if "agent_start" not in names:
            assert "write_file" not in names and "run_command" not in names
            child_tasks.append(conversation[-1]["content"])
            yield "content", "Read-only review: simplify the constant in calc.py."
            return
        lead_turns += 1
        if lead_turns == 1:
            assert "Layla /simplify" in conversation[-1]["content"]
            yield "tool_calls", [call("agent_start", task=f"Review {axis} of calc.py; VALUE changed to (1 + 1). Report only.", role="review")
                                 for axis in ("reuse", "quality", "efficiency")]
        elif lead_turns == 2:
            yield "content", "Collecting the three reviews."
        elif lead_turns == 3:
            assert "workers_completed" in conversation[-1]["content"]
            assert len(child_tasks) == 3
            yield "tool_calls", [call("write_file", path="calc.py", content="VALUE = 2\n", expected_sha256=original["sha256"])]
        else:
            yield "content", "Simplified the constant after review. No test command was run."

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    started = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "/simplify calc.py"})
    assert started.status_code == 202
    await finish(client, started.json()["id"])
    assert (root / "calc.py").read_text() == "VALUE = 2\n"
    manager = code_workers.Manager(runtime, str(root), provider_id=provider["id"], model="m")
    workers = await manager.list()
    assert len(workers) == 3 and all(row["role"] == "review" and row["status"] == "done" for row in workers)
    assert lead_turns == 4
