"""Behavioral tests for durable Code state, notebook edits and specialist concurrency."""
from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

from app.models.chat import Chat
from app.models.code_state import CodeState
from app.services import code_mcp, code_notebooks, code_state, files, mcp_client, project_agent


def transition(state, name, **arguments):
    candidate = copy.deepcopy(state)
    result = code_state.apply(candidate, name, code_state.SCHEMAS[name].model_validate(arguments))
    state.clear()
    state.update(candidate)
    return result


def test_dependencies_cannot_cycle_or_complete_out_of_order():
    state = code_state.empty_state()
    first = transition(state, "task_create", subject="Implement")['task']['id']
    second = transition(state, "task_create", subject="Verify", blocked_by=[first])['task']['id']
    assert transition(state, "task_get", task_id=second)["task"]["blocked"]
    before = copy.deepcopy(state)
    for status in ("in_progress", "completed"):
        with pytest.raises(ValueError, match="зависимости"):
            transition(state, "task_update", task_id=second, status=status)
    with pytest.raises(ValueError, match="цикл"):
        transition(state, "task_update", task_id=first, blocked_by=[second])
    assert state == before
    transition(state, "task_update", task_id=first, status="completed", result="Unit tests passed")
    assert transition(state, "task_get", task_id=second)["task"]["ready"]
    transition(state, "task_update", task_id=second, status="completed")
    with pytest.raises(ValueError, match="зависимости"):
        transition(state, "task_update", task_id=first, status="pending")
    with pytest.raises(ValueError, match="ссылаются"):
        transition(state, "task_update", task_id=first, status="deleted")


def test_task_metadata_aliases_and_reverse_dependencies():
    state = code_state.empty_state()
    first = transition(state, "task_create", subject="Implement", activeForm="Implementing",
                       metadata={"obsolete": 1})["task"]["id"]
    second = transition(state, "task_create", subject="Verify")["task"]["id"]
    transition(state, "task_update", taskId=first, addBlocks=[second],
               metadata={"obsolete": None, "test_command": "pytest"})
    task = transition(state, "task_get", taskId=first)["task"]
    assert task["blocks"] == [second] and task["metadata"] == {"test_command": "pytest"}
    assert task["active_form"] == "Implementing"
    assert transition(state, "task_get", taskId=second)["task"]["blocked_by"] == [first]


async def setup_code(client):
    response = await client.post("/api/auth/register", json={
        "email": "runtime@example.com", "password": "hunter2hunter2",
    })
    assert response.status_code == 201, response.text
    owner = response.json()["id"]
    response = await client.post("/api/projects", json={"name": "Runtime project"})
    assert response.status_code == 201, response.text
    project = response.json()
    response = await client.post("/api/chats", json={
        "domain": "code", "project_id": project["id"], "model": "m",
    })
    assert response.status_code == 201, response.text
    return owner, project, response.json()


async def test_state_survives_instances_and_is_scoped_to_chat_and_owner(client, db_sessionmaker):
    owner, project, chat = await setup_code(client)
    runtime = code_state.Runtime(db_sessionmaker, owner, chat["id"], project["id"])
    task = await runtime.call("task_create", {"subject": "Build API"})
    assert "error" not in task
    task_id = task["task"]["id"]
    await runtime.call("memory_write", {"key": "decision", "content": "Use pagination"})
    restored = code_state.Runtime(db_sessionmaker, owner, chat["id"], project["id"])
    assert (await restored.call("task_get", {"task_id": task_id}))["task"]["subject"] == "Build API"
    assert "Use pagination" in await restored.context()
    state = await client.get(f"/api/chats/{chat['id']}/code-state")
    assert state.status_code == 200 and state.json()["tasks"][0]["id"] == task_id
    other_chat = (await client.post("/api/chats", json={
        "domain": "code", "project_id": project["id"], "model": "m",
    })).json()
    isolated = code_state.Runtime(db_sessionmaker, owner, other_chat["id"], project["id"])
    assert "error" in await isolated.call("task_get", {"task_id": task_id})
    assert (await isolated.read())["memory"] == {}
    impostor = code_state.Runtime(db_sessionmaker, "someone-else", chat["id"], project["id"])
    assert "error" in await impostor.call("task_list", {})
    with pytest.raises(ValueError):
        await impostor.read()
    async with db_sessionmaker() as session:
        await session.execute(delete(Chat).where(Chat.id == chat["id"]))
        await session.commit()
        assert await session.get(CodeState, chat["id"]) is None


async def test_parallel_state_updates_do_not_lose_notes(client, db_sessionmaker):
    owner, project, chat = await setup_code(client)
    runtimes = [code_state.Runtime(db_sessionmaker, owner, chat["id"], project["id"]) for _ in range(4)]
    results = await asyncio.gather(*[
        runtime.call("memory_write", {"key": f"note-{index}", "content": f"decision {index}"})
        for index, runtime in enumerate(runtimes)
    ])
    assert all("error" not in result for result in results), results
    assert len((await runtimes[0].read())["memory"]) == 4


async def test_runtime_rejects_project_switch_and_other_domains(client, db_sessionmaker):
    owner, project, chat = await setup_code(client)
    second = (await client.post("/api/projects", json={"name": "Other"})).json()
    runtime = code_state.Runtime(db_sessionmaker, owner, chat["id"], second["id"])
    assert "error" in await runtime.call("task_create", {"subject": "Wrong project"})
    design = (await client.post("/api/chats", json={"domain": "design", "model": "m"})).json()
    assert (await client.get(f"/api/chats/{design['id']}/code-state")).status_code == 404
    runtime = code_state.Runtime(db_sessionmaker, owner, design["id"], project["id"])
    assert "error" in await runtime.call("task_list", {})


async def test_code_state_http_endpoint_does_not_cross_accounts(client, db_sessionmaker):
    owner, project, chat = await setup_code(client)
    runtime = code_state.Runtime(db_sessionmaker, owner, chat["id"], project["id"])
    await runtime.call("memory_write", {"key": "private", "content": "Account A decision"})
    registered = await client.post("/api/auth/register", json={
        "email": "other-runtime@example.com", "password": "hunter2hunter2",
    })
    assert registered.status_code == 201
    response = await client.get(f"/api/chats/{chat['id']}/code-state")
    assert response.status_code == 404
    assert "Account A decision" not in response.text


async def test_structured_question_answer_survives_navigation_and_resumes_waiter(client, db_sessionmaker):
    owner, project, chat = await setup_code(client)
    runtime = code_state.Runtime(db_sessionmaker, owner, chat["id"], project["id"])
    group = (await runtime.call("ask_user", {"questions": [{
        "header": "Storage", "question": "Which database should the app use?",
        "options": [{"label": "PostgreSQL"}, {"label": "SQLite"}],
    }]}))["question"]
    restored = code_state.Runtime(db_sessionmaker, owner, chat["id"], project["id"])
    waiter = asyncio.create_task(restored.wait_for_answer(group["id"], timeout=5))
    invalid = await client.post(f"/api/chats/{chat['id']}/code-state/answer", json={
        "question_id": group["id"], "answers": {},
    })
    assert invalid.status_code == 422
    submitted = await client.post(f"/api/chats/{chat['id']}/code-state/answer", json={
        "question_id": group["id"], "answers": {"0": ["PostgreSQL"]},
    })
    assert submitted.status_code == 200 and submitted.json()["resume_needed"] is True
    result = await asyncio.wait_for(waiter, timeout=5)
    assert result["question"]["answers"] == {"0": ["PostgreSQL"]}
    assert "PostgreSQL" in await restored.context()
    foreign = await client.post(f"/api/chats/{chat['id']}/code-state/answer", json={
        "question_id": "unknown", "answers": {"0": ["SQLite"]},
    })
    assert foreign.status_code == 422


async def test_live_chat_question_unblocks_same_worker(client, db_sessionmaker, monkeypatch):
    _owner, _project, chat = await setup_code(client)
    await client.post("/api/providers", json={
        "name": "Question mock", "kind": "openai_compatible", "base_url": "http://mock/v1",
        "default_model": "m", "active": True,
    })
    turns = 0

    async def stream(_provider, _key, _model, conversation, _available, **_kwargs):
        nonlocal turns
        turns += 1
        if turns == 1:
            yield "tool_calls", [call("ask_user", {"questions": [{
                "header": "DB", "question": "Choose database", "options": [{"label": "PG"}, {"label": "SQLite"}],
            }]})]
        else:
            answer = json.loads(conversation[-1]["content"])
            assert answer["question"]["answers"]["0"] == ["SQLite"]
            yield "content", "Continuing with SQLite"

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    started = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "Create a database app"})
    assert started.status_code == 202
    for _ in range(200):
        state = (await client.get(f"/api/chats/{chat['id']}/code-state")).json()
        if state.get("questions"):
            break
        await asyncio.sleep(0.01)
    answer = await client.post(f"/api/chats/{chat['id']}/code-state/answer", json={
        "question_id": state["questions"][0]["id"], "answers": {"0": ["SQLite"]},
    })
    assert answer.status_code == 200 and answer.json()["resume_needed"] is False
    for _ in range(200):
        job = (await client.get(f"/api/jobs/{started.json()['id']}")).json()
        if job["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.01)
    assert job["status"] == "done", job
    assert turns == 2


async def test_task_graph_persists_through_real_background_chat(client, db_sessionmaker, monkeypatch):
    _owner, _project, chat = await setup_code(client)
    await client.post("/api/providers", json={
        "name": "Mock", "kind": "openai_compatible", "base_url": "http://mock/v1",
        "default_model": "m", "active": True,
    })
    turns = 0

    async def stream(_provider, _key, _model, conversation, available, **_kwargs):
        nonlocal turns
        turns += 1
        assert "task_create" in {tool["function"]["name"] for tool in available}
        if turns == 1:
            yield "tool_calls", [call("task_create", {"subject": "Verify code", "description": "Run checks"})]
        else:
            assert json.loads(conversation[-1]["content"])["task"]["subject"] == "Verify code"
            yield "content", "Task recorded"

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream)
    response = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "Record work"})
    assert response.status_code == 202, response.text
    for _ in range(200):
        state = (await client.get(f"/api/jobs/{response.json()['id']}")).json()
        if state["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.01)
    assert state["status"] == "done", state
    persisted = (await client.get(f"/api/chats/{chat['id']}/code-state")).json()
    assert persisted["tasks"][0]["subject"] == "Verify code"
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    assert detail["messages"][-1]["meta"]["todos"][0]["content"] == "Verify code"


def notebook(tmp_path):
    document = {"nbformat": 4, "nbformat_minor": 5, "metadata": {"custom": "keep"}, "cells": [
        {"id": "intro", "cell_type": "markdown", "source": ["# Example"], "metadata": {}},
        {"id": "calc", "cell_type": "code", "source": ["1 + 1"], "metadata": {"tags": ["keep"]},
         "execution_count": 3, "outputs": [{"output_type": "stream", "name": "stdout", "text": ["2"]}]},
    ]}
    (tmp_path / "demo.ipynb").write_text(json.dumps(document))
    return document


def test_notebook_changes_only_requested_cell_and_rejects_stale_version(tmp_path):
    before = notebook(tmp_path)
    read = code_notebooks.execute(str(tmp_path), "notebook_read", {"path": "demo.ipynb", "offset": 1})
    assert "outputs" not in read["cells"][0]
    args = {"path": "demo.ipynb", "expected_sha256": read["sha256"], "cell_index": 1, "source": "2 + 2"}
    result = code_notebooks.execute(str(tmp_path), "notebook_edit", args)
    assert result["change"]["operation"] == "edit"
    after = json.loads((tmp_path / "demo.ipynb").read_text())
    assert after["metadata"] == before["metadata"] and after["cells"][0] == before["cells"][0]
    assert after["cells"][1]["id"] == "calc" and after["cells"][1]["metadata"] == {"tags": ["keep"]}
    assert after["cells"][1]["outputs"] == [] and after["cells"][1]["execution_count"] is None
    with pytest.raises(files.FileConflict):
        code_notebooks.execute(str(tmp_path), "notebook_edit", args)
    with pytest.raises(ValueError):
        code_notebooks.execute(str(tmp_path), "notebook_read", {"path": "../demo.ipynb"})


def call(name, args, ident="call"):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


async def test_notebook_confirm_mode_rejects_without_writing_and_saves_checkpoint(tmp_path, monkeypatch):
    before = notebook(tmp_path)
    current = files.read_file(str(tmp_path), "demo.ipynb")
    edit = call("notebook_edit", {"path": "demo.ipynb", "expected_sha256": current["sha256"],
                                  "cell_index": 1, "source": "print(4)"})

    async def turn(_provider, _key, _model, conversation, available, _caps, _anchor):
        if len(conversation) == 2:
            assert "notebook_edit" in {tool["function"]["name"] for tool in available}
            yield "done", ([], [], {}, [edit])
        else:
            yield "done", (["Done"], [], {}, [])

    async def context():
        return ""

    async def reject(event):
        assert "+   " in event["change"]["diff"] or "print(4)" in event["change"]["diff"]
        return "reject"

    monkeypatch.setattr(project_agent, "_turn", turn)
    runtime = SimpleNamespace(context=context)
    events = [event async for event in project_agent.run(None, None, "m", [{"role": "user", "content": "edit"}],
              str(tmp_path), mode="confirm", approve=reject, runtime=runtime)]
    assert any(event.get("tool", {}).get("status") == "rejected" for event in events)
    assert json.loads((tmp_path / "demo.ipynb").read_text()) == before
    assert not any("checkpoint" in event for event in events)
    events = [event async for event in project_agent.run(None, None, "m", [{"role": "user", "content": "edit"}],
              str(tmp_path), mode="auto", runtime=runtime)]
    assert next(event["checkpoint"] for event in events if "checkpoint" in event)["content"] == current["content"]
    assert "print(4)" in (tmp_path / "demo.ipynb").read_text()


async def test_specialists_run_concurrently_and_keep_result_order(tmp_path, monkeypatch):
    entered = set()
    barrier = asyncio.Event()

    async def specialist(_provider, _key, _model, _root, request, _caps, _extra, progress=None):
        entered.add(request.task)
        if len(entered) == 3:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), timeout=2)
        await progress({"tool": "read_file", "round": 1, "tool_calls": 1})
        return {"role": request.role, "report": request.task, "rounds": 1, "tool_calls": 1}

    batch = [call("delegate_agent", {"task": f"Investigate module {i}"}, f"c{i}") for i in range(3)]
    monkeypatch.setattr(project_agent, "_delegate_agent", specialist)
    events = [event async for event in project_agent._parallel_delegates(
        batch, 3, 0, None, None, "m", str(tmp_path), {}, "",
    )]
    assert len(entered) == 3
    assert len([event for event in events if "delegate_result" in event]) == 3
    assert any("read_file" in event.get("tool", {}).get("note", "") for event in events)


async def test_stopping_parent_cancels_every_specialist(tmp_path, monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(project_agent, "_delegate_agent", blocked)

    async def parent():
        async for _event in project_agent._parallel_delegates(
            [call("delegate_agent", {"task": "Investigate something"})], 1, 0,
            None, None, "m", str(tmp_path), {}, "",
        ):
            pass

    task = asyncio.create_task(parent())
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_runtime_tools_do_not_leak_into_other_domains_or_readonly_mode(tmp_path, monkeypatch):
    names = []

    async def turn(_provider, _key, _model, _conversation, available, _caps, _anchor):
        names.append({tool["function"]["name"] for tool in available})
        yield "done", (["Done"], [], {}, [])

    async def context():
        return ""

    monkeypatch.setattr(project_agent, "_turn", turn)
    base = {tool["function"]["name"] for tool in project_agent.TOOLS}
    for runtime, mode, permissions in [(SimpleNamespace(context=context), "plan", None),
                                        (SimpleNamespace(context=context), "auto", []),
                                        (None, "auto", None)]:
        _ = [event async for event in project_agent.run(None, None, "m", [{"role": "user", "content": "hi"}],
             str(tmp_path), permissions, runtime=runtime, mode=mode)]
    assert "notebook_read" in names[0] and "notebook_edit" not in names[0]
    assert "task_list" not in names[1] and "notebook_edit" not in names[1]
    assert names[2] == base
    assert {tool["function"]["name"] for tool in project_agent.TOOLS} == base


async def test_mcp_long_names_collisions_and_resources_are_routed(monkeypatch):
    def server(ident):
        return SimpleNamespace(id=ident, name="a" * 40, transport="http", command=None,
                               url="https://mcp.example", env_secret_ref=None)

    async def listed(**_kwargs):
        return [{"name": "b" * 100, "description": "Docs", "input_schema": {"type": "object"}}]

    async def resources(**_kwargs):
        return {"resources": [{"uri": "docs://guide"}], "next_cursor": None}

    monkeypatch.setattr(mcp_client, "list_tools", listed)
    monkeypatch.setattr(mcp_client, "list_resources", resources)
    agent = await code_mcp.McpAgent.create([server("one"), server("two")])
    assert len(agent.routes) == 2
    assert all(len(name) <= 64 for name in agent.names)
    found = await agent.call("mcp_search_tools", {"query": "Docs"})
    assert {item["id"] for item in found["servers"]} == {"one", "two"}
    assert (await agent.call("mcp_list_resources", {"server": "one"}))["resources"][0]["uri"] == "docs://guide"
    assert "error" in await agent.call("mcp_list_resources", {"server": "someone-else"})


async def test_mcp_plain_text_results_are_supported_only_when_requested(monkeypatch):
    async def call_tool(_name, arguments):
        return SimpleNamespace(isError=False, structuredContent=None,
                               content=[SimpleNamespace(type="text", text="Documentation result")])

    @asynccontextmanager
    async def session(**_kwargs):
        yield SimpleNamespace(call_tool=call_tool)

    monkeypatch.setattr(mcp_client, "_session", session)
    result = await mcp_client.call_tool(name="docs", arguments={}, transport="http", allow_text=True)
    assert result["text"] == "Documentation result"
    with pytest.raises(RuntimeError):
        await mcp_client.call_tool(name="docs", arguments={}, transport="http")


async def test_mcp_tool_inventory_follows_pagination(monkeypatch):
    cursors = []

    async def list_tools(cursor=None):
        cursors.append(cursor)
        return SimpleNamespace(tools=[SimpleNamespace(name="first" if cursor is None else "second",
                                                      description="", inputSchema={"type": "object"})],
                               nextCursor="page2" if cursor is None else None)

    @asynccontextmanager
    async def session(**_kwargs):
        yield SimpleNamespace(list_tools=list_tools)

    monkeypatch.setattr(mcp_client, "_session", session)
    result = await mcp_client.list_tools(transport="http", command=None, url="https://mcp.example")
    assert [tool["name"] for tool in result] == ["first", "second"]
    assert cursors == [None, "page2"]


def test_skills_support_standard_directories_without_symlink_access(tmp_path):
    from app.services import code_intelligence

    for directory in (".claude/skills/build", ".agents/skills/test", ".layla/skills/build"):
        path = tmp_path / directory
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(directory)
    assert code_intelligence.list_skills(str(tmp_path), code_intelligence.ListSkills()) == {"skills": ["build", "test"]}
    assert code_intelligence.read_skill(str(tmp_path), code_intelligence.ReadSkill(name="build"))["content"] == ".layla/skills/build"
    (tmp_path / ".layla/skills/leak").symlink_to(tmp_path.parent)
    assert "leak" not in code_intelligence.list_skills(str(tmp_path), code_intelligence.ListSkills())["skills"]
