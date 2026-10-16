from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import code_instructions, project_agent


def write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def paths(result):
    return [item["path"] for item in result["instructions"]]


def test_root_precedence_nested_scope_and_freshness(tmp_path):
    for path in ["CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md", "LAYLA.md",
                 ".claude/rules/common.md", "src/CLAUDE.md", "other/CLAUDE.md"]:
        write(tmp_path, path, path)
    book = code_instructions.Book(str(tmp_path))
    assert paths(book.for_path()) == ["CLAUDE.md", ".claude/CLAUDE.md", ".claude/rules/common.md",
                                     "CLAUDE.local.md", "LAYLA.md"]
    assert paths(book.for_path("src/new.py", fresh=True)) == ["src/CLAUDE.md"]
    assert not book.for_path("src/next.py", fresh=True)["instructions"]
    assert "other/CLAUDE.md" not in paths(book.for_path("src/new.py"))
    assert "src/CLAUDE.md" not in paths(book.for_path("other/new.py"))


def test_rules_in_nested_directories_and_conditional_globs(tmp_path):
    write(tmp_path, ".claude/rules/python.md", "---\npaths: ['src/**/*.py']\n---\nRoot Python rules")
    write(tmp_path, "src/.layla/rules/deep/test.md", "---\npaths: ['tests/**']\n---\nTest rules")
    book = code_instructions.Book(str(tmp_path))
    assert not book.for_path()["instructions"]
    assert paths(book.for_path("src/lib/main.py")) == [".claude/rules/python.md"]
    assert paths(book.for_path("src/tests/test.py")) == [".claude/rules/python.md", "src/.layla/rules/deep/test.md"]
    assert not book.for_path("docs/test.py")["instructions"]


@pytest.mark.parametrize(("path", "pattern", "expected"), [
    ("src/main.py", "src/**/*.py", True), ("src/lib/main.py", "src/*.py", False),
    ("src/lib/main.py", "src/**", True), ("src/lib/main.py", "**/**/**/**/x", False),
])
def test_segment_globs(path, pattern, expected):
    assert code_instructions.matches(path, pattern) is expected


def test_imports_stay_within_project_ignore_code_and_bound_cycles(tmp_path):
    write(tmp_path, "CLAUDE.md", "@./docs/rules.md\n`@inline.md`\n```sh\n@fenced.md\n```\n@../outside.md\n")
    write(tmp_path, "docs/rules.md", "Imported rules\n@../CLAUDE.md")
    write(tmp_path, "inline.md", "IGNORE_INLINE")
    write(tmp_path, "fenced.md", "IGNORE_FENCE")
    write(tmp_path, "link.md", "IGNORE_LINK")
    (tmp_path / "linked.md").symlink_to(tmp_path / "link.md")
    write(tmp_path, "LAYLA.md", "@linked.md")
    result = code_instructions.Book(str(tmp_path)).for_path()
    assert set(paths(result)) == {"CLAUDE.md", "docs/rules.md", "LAYLA.md"}
    encoded = json.dumps(result)
    assert "IGNORE_" not in encoded
    assert result["warnings"]


def test_new_book_picks_up_edits_but_current_snapshot_does_not(tmp_path):
    write(tmp_path, "CLAUDE.md", "first")
    book = code_instructions.Book(str(tmp_path))
    assert book.for_path()["instructions"][0]["content"] == "first"
    write(tmp_path, "CLAUDE.md", "second")
    assert book.for_path()["instructions"][0]["content"] == "first"
    assert code_instructions.Book(str(tmp_path)).for_path()["instructions"][0]["content"] == "second"


def test_include_cannot_widen_conditional_scope(tmp_path):
    write(tmp_path, ".claude/rules/api.md", "---\npaths: ['api/**']\n---\n@../../shared.md")
    write(tmp_path, "shared.md", "---\npaths: ['**/*.py']\n---\nONLY_API_PYTHON")
    book = code_instructions.Book(str(tmp_path))
    assert "ONLY_API_PYTHON" not in json.dumps(book.for_path("web/main.py"))
    assert "ONLY_API_PYTHON" not in json.dumps(book.for_path("api/main.ts"))
    assert "ONLY_API_PYTHON" in json.dumps(book.for_path("api/main.py"))


def test_limits_and_malformed_frontmatter_are_reported(tmp_path, monkeypatch):
    write(tmp_path, "CLAUDE.md", "---\npaths: &p ['**']\n---\ninvalid")
    write(tmp_path, "LAYLA.md", "x" * 100)
    monkeypatch.setattr(code_instructions, "MAX_FILE_CHARS", 10)
    result = code_instructions.Book(str(tmp_path)).for_path()
    assert paths(result) == ["LAYLA.md"]
    assert result["instructions"][0]["content"] == "x" * 10
    assert result["instructions"][0]["truncated"]
    assert len(result["warnings"]) == 2


async def test_agent_reviews_nested_instructions_before_writing(tmp_path, monkeypatch):
    write(tmp_path, "CLAUDE.md", "ROOT_GUIDANCE")
    write(tmp_path, "src/CLAUDE.md", "NESTED_GUIDANCE")
    turns = 0
    hook_events = []

    async def prepare():
        return ""

    async def fire(event, data, _ask):
        hook_events.append((event, data))
        yield {"hook_result": {}}

    workflow = SimpleNamespace(runner=SimpleNamespace(root=str(tmp_path)), prepare=prepare,
                               fire=fire, slash=lambda _: None, tools=list)

    async def turn(_provider, _key, _model, conversation, available, *_args):
        nonlocal turns
        turns += 1
        assert "ROOT_GUIDANCE" in conversation[0]["content"]
        assert "project_instructions" in {t["function"]["name"] for t in available}
        if turns == 2:
            assert not (tmp_path / "src/new.txt").exists()
            result = json.loads(conversation[-1]["content"])
            assert result["retry_required"]
            assert "NESTED_GUIDANCE" in json.dumps(result)
        if turns >= 2:
            assert conversation[0]["content"].count("NESTED_GUIDANCE") == 1
        calls = [{"id": str(turns), "type": "function", "function": {
            "name": "write_file", "arguments": json.dumps({"path": "src/new.txt", "content": "created"}),
        }}] if turns < 3 else []
        yield "done", (["done"] if not calls else [], [], {}, calls)

    monkeypatch.setattr(project_agent, "_turn", turn)
    events = [event async for event in project_agent.run(None, None, "mock",
        [{"role": "user", "content": "Create src/new.txt"}], str(tmp_path), worker_context=True,
        workflow=workflow)]
    assert (tmp_path / "src/new.txt").read_text() == "created"
    assert sum("checkpoint" in event for event in events) == 1
    assert sum(event == "PostToolUse" and data["tool_name"] == "write_file"
               for event, data in hook_events) == 1


@pytest.mark.parametrize(("worker_context", "permissions"), [(False, None), (True, [])])
async def test_instructions_do_not_expand_domain_or_persona_access(tmp_path, monkeypatch, worker_context, permissions):
    write(tmp_path, "CLAUDE.md", "PRIVATE_GUIDANCE")

    async def turn(_provider, _key, _model, conversation, available, *_args):
        assert "PRIVATE_GUIDANCE" not in conversation[0]["content"]
        assert "project_instructions" not in {t["function"]["name"] for t in available}
        yield "done", (["done"], [], {}, [])

    monkeypatch.setattr(project_agent, "_turn", turn)
    _events = [event async for event in project_agent.run(None, None, "mock",
        [{"role": "user", "content": "hello"}], str(tmp_path), permissions,
        worker_context=worker_context)]
