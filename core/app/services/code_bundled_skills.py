"""Immutable Code skill assets shipped with Core; no project/home filesystem reads."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from importlib.resources import files

MANIFEST = {
    "simplify": {"SKILL.md": "simplify.md"},
    "skillify": {"SKILL.md": "skillify.md", "templates/SKILL.md": "skillify-template.md"},
    "remember": {"SKILL.md": "remember.md"},
}


@lru_cache(maxsize=1)
def documents():
    directory = files("app.services").joinpath("prompts", "skills")
    return tuple((name, tuple((path, directory.joinpath(asset).read_text(encoding="utf-8"))
                             for path, asset in assets.items())) for name, assets in MANIFEST.items())


def document(content):
    return {"content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
