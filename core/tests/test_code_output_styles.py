import json

import pytest

from app.services import code_output_styles, project_agent
from tests.test_code_instructions import write


def test_project_style_and_settings_precedence(tmp_path):
    write(tmp_path, ".claude/settings.json", '{"outputStyle":"Explanatory"}')
    write(tmp_path, ".layla/settings.local.json", '{"outputStyle":"Short"}')
    write(tmp_path, ".claude/output-styles/short.md", "---\nname: Short\n---\nOld style")
    write(tmp_path, ".layla/output-styles/short.md", "---\nname: Short\nkeep-coding-instructions: true\n---\nSHORT_STYLE")
    catalog = code_output_styles.Catalog(str(tmp_path))
    assert catalog.public()["active"] == "Short"
    assert "SHORT_STYLE" in catalog.prompt() and "Old style" not in catalog.prompt()
    assert "SHORT_STYLE" not in json.dumps(catalog.public())


def test_unavailable_and_malformed_styles_are_explicit(tmp_path):
    write(tmp_path, ".layla/settings.json", '{"outputStyle":"Missing"}')
    write(tmp_path, ".claude/settings.json", '{"outputStyle":123}')
    write(tmp_path, ".layla/output-styles/bad.md", "---\nname: &alias bad\n---\nBAD_STYLE")
    catalog = code_output_styles.Catalog(str(tmp_path))
    assert catalog.public()["active"] == "default"
    assert len(catalog.public()["warnings"]) == 3
    assert "BAD_STYLE" not in catalog.prompt()


def test_style_symlinks_are_not_read(tmp_path):
    write(tmp_path, ".layla/settings.json", '{"outputStyle":"linked"}')
    write(tmp_path, "private.md", "PRIVATE_STYLE")
    (tmp_path / ".layla/output-styles").mkdir()
    (tmp_path / ".layla/output-styles/linked.md").symlink_to(tmp_path / "private.md")
    catalog = code_output_styles.Catalog(str(tmp_path))
    assert "PRIVATE_STYLE" not in catalog.prompt()
    assert catalog.public()["active"] == "default"


@pytest.mark.parametrize("code", [True, False])
async def test_styles_reach_code_provider_only(tmp_path, monkeypatch, code):
    write(tmp_path, ".layla/settings.json", '{"outputStyle":"short"}')
    write(tmp_path, ".layla/output-styles/short.md", "ACTIVE_OUTPUT_STYLE")

    async def turn(_provider, _key, _model, conversation, available, *_args):
        assert ("ACTIVE_OUTPUT_STYLE" in conversation[0]["content"]) is code
        assert ("list_output_styles" in {t["function"]["name"] for t in available}) is code
        yield "done", (["done"], [], {}, [])

    monkeypatch.setattr(project_agent, "_turn", turn)
    _events = [event async for event in project_agent.run(None, None, "mock",
        [{"role": "user", "content": "hello"}], str(tmp_path), worker_context=code)]
