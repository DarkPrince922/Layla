import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services import (
    code_intelligence,
    code_mcp,
    code_research,
    mcp_client,
    project_agent,
    web_research,
)


def test_repo_navigation_is_bounded_and_pageable(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "class Application:\n    pass\n\ndef serve():\n    return 'needle'\n"
    )
    (tmp_path / "src" / "other.py").write_text("NEEDLE = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "noise.py").write_text("needle")

    found = code_intelligence.execute(
        str(tmp_path), "glob_files", {"pattern": "*.py", "path": ".", "limit": 1}
    )
    assert found == {
        "files": ["src/main.py"], "count": 1, "total": 2, "offset": 0, "truncated": True
    }
    second = code_intelligence.execute(
        str(tmp_path), "glob_files", {"pattern": "*.py", "offset": 1}
    )
    assert second["files"] == ["src/other.py"]

    matches = code_intelligence.execute(
        str(tmp_path), "grep_files",
        {"pattern": "needle", "glob": "*.py", "-i": True, "output_mode": "count"},
    )
    assert matches["matched_files"] == 2
    assert {row["path"] for row in matches["matches"]} == {"src/main.py", "src/other.py"}

    mapped = code_intelligence.execute(str(tmp_path), "repo_map", {})
    main = next(item for item in mapped["files"] if item["path"] == "src/main.py")
    assert main["symbols"] == ["Application", "serve"]
    assert all("node_modules" not in item["path"] for item in mapped["files"])


def test_navigation_never_follows_links_or_parent_paths(tmp_path):
    outside = tmp_path.parent / "outside-code.txt"
    outside.write_text("secret needle")
    (tmp_path / "linked").symlink_to(outside)
    assert code_intelligence.execute(str(tmp_path), "glob_files", {"pattern": "*"})["files"] == []
    with pytest.raises((ValueError, ValidationError)):
        code_intelligence.execute(str(tmp_path), "grep_files", {"path": "../", "pattern": "secret"})


def test_project_skills_and_ranged_read(tmp_path):
    skill_dir = tmp_path / ".layla" / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "release.md").write_text("Run tests, then build.")
    (tmp_path / "long.txt").write_text("one\ntwo\nthree\nfour\n")

    assert code_intelligence.execute(str(tmp_path), "list_skills", {}) == {"skills": ["release"]}
    assert code_intelligence.execute(str(tmp_path), "read_skill", {"name": "release"})["content"] == "Run tests, then build."
    read = project_agent.execute(str(tmp_path), "read_file", {"path": "long.txt", "offset": 2, "limit": 2})
    assert read["content"] == "two\nthree\n"
    assert read["total_lines"] == 4 and read["truncated"] is True and len(read["sha256"]) == 64


def test_code_tools_require_read_permission(tmp_path):
    names = {tool["function"]["name"] for tool in project_agent.permitted_tools([])}
    assert not ({"glob_files", "grep_files", "repo_map", "list_skills", "read_skill", "delegate_agent"} & names)


async def test_delegate_is_isolated_readonly_and_reports_to_lead(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("def start():\n    pass\n")
    turns = 0

    async def fake_turn(_provider, _key, _model, conversation, available, _caps, _anchor):
        nonlocal turns
        turns += 1
        names = {tool["function"]["name"] for tool in available}
        assert "delegate_agent" not in names and "write_file" not in names and "run_command" not in names
        if turns == 1:
            yield "done", ([], [], {}, [{
                "id": "find", "type": "function",
                "function": {"name": "glob_files", "arguments": json.dumps({"pattern": "*.py"})},
            }])
        else:
            result = json.loads(conversation[-1]["content"])
            assert result["files"] == ["app.py"]
            yield "done", (["Use app.py:start."], [], {}, [])

    monkeypatch.setattr(project_agent, "_turn", fake_turn)
    outcome = await project_agent._delegate_agent(
        object(), "key", "model", str(tmp_path),
        project_agent.DelegateAgent(task="Find the application entry point", role="explore"), {}, "",
    )
    assert outcome == {"role": "explore", "report": "Use app.py:start.", "rounds": 2, "tool_calls": 1}


async def test_code_research_maps_public_tools(monkeypatch):
    async def search(query, blocked=()):
        return {"query": query, "blocked": list(blocked)}

    async def page(url, blocked=()):
        return {"url": url, "blocked": list(blocked)}

    monkeypatch.setattr(web_research, "search_web", search)
    monkeypatch.setattr(web_research, "search_github", search)
    monkeypatch.setattr(web_research, "read_web_page", page)
    assert (await code_research.execute("web_search", {"query": "Python 3.13 docs"}))["query"] == "Python 3.13 docs"
    assert (await code_research.execute("github_search", {"query": "fastapi examples"}))["query"] == "fastapi examples"
    assert (await code_research.execute("web_fetch", {"url": "https://example.com/docs"}))["url"].endswith("/docs")


async def test_mcp_tools_are_namespaced_and_called(monkeypatch):
    server = SimpleNamespace(name="Docs server", transport=SimpleNamespace(value="http"),
                             command=None, url="https://mcp.example", env_secret_ref=None)

    async def listed(**_kwargs):
        return [{"name": "lookup", "description": "Find docs",
                 "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}]

    async def called(**kwargs):
        return {"tool": kwargs["name"], "q": kwargs["arguments"]["q"]}

    monkeypatch.setattr(mcp_client, "list_tools", listed)
    monkeypatch.setattr(mcp_client, "call_tool", called)
    agent = await code_mcp.McpAgent.create([server])
    assert agent is not None
    assert agent.specs[0]["function"]["name"] == "mcp__Docs_server__lookup"
    assert await agent.call("mcp__Docs_server__lookup", {"q": "routing"}) == {
        "tool": "lookup", "q": "routing"
    }
