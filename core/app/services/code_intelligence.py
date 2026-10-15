"""Fast, project-scoped navigation tools for the Code agent.

All traversal starts from a path validated by ``files.safe_join`` and ignores
symlinks.  The model receives bounded, pageable results so a broad search cannot
consume the whole context window.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services import files

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist", "build"}
MAX_RESULTS = 500
MAX_SCAN_FILES = 20_000
MAX_SEARCH_BYTES = 2_000_000
MAX_RESULT_CHARS = 60_000


class GlobFiles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(min_length=1, max_length=500)
    path: str = Field(default=".", max_length=4096)
    limit: int = Field(default=100, ge=1, le=MAX_RESULTS)
    offset: int = Field(default=0, ge=0, le=100_000)


class GrepFiles(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    pattern: str = Field(min_length=1, max_length=2000)
    path: str = Field(default=".", max_length=4096)
    glob: str | None = Field(default=None, max_length=500)
    output_mode: str = Field(default="content", pattern=r"^(content|files_with_matches|count)$")
    case_insensitive: bool = Field(default=False, alias="-i")
    context: int = Field(default=0, ge=0, le=20)
    head_limit: int = Field(default=100, ge=1, le=MAX_RESULTS)
    offset: int = Field(default=0, ge=0, le=100_000)

    @field_validator("pattern")
    @classmethod
    def valid_regex(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"некорректное регулярное выражение: {exc}") from exc
        return value


class RepoMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(default=".", max_length=4096)
    depth: int = Field(default=4, ge=1, le=8)
    max_files: int = Field(default=400, ge=20, le=2000)


class ListSkills(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")


SCHEMAS = {
    "glob_files": GlobFiles,
    "grep_files": GrepFiles,
    "repo_map": RepoMap,
    "list_skills": ListSkills,
    "read_skill": ReadSkill,
}

DESCRIPTIONS = {
    "glob_files": "Find project files by glob pattern. Results are sorted, bounded and pageable with offset.",
    "grep_files": "Search project text with a regular expression. Supports content, files_with_matches and count output, a file glob, context lines and pagination.",
    "repo_map": "Build a compact repository map: important files and top-level classes/functions. Use near the start of work on an unfamiliar or large project.",
    "list_skills": "List optional project workflows stored in .layla/skills/*.md.",
    "read_skill": "Read one project workflow from .layla/skills/<name>.md when it matches the task.",
}

TOOLS = [
    {"type": "function", "function": {"name": name, "description": DESCRIPTIONS[name],
     "parameters": schema.model_json_schema()}}
    for name, schema in SCHEMAS.items()
]


def _walk(root: str, rel: str):
    """Walk with the same no-follow descriptor operations as the editor."""
    files.safe_join(root, rel)  # lexical validation before recursive expansion
    pending = ["/".join(files._parts(rel)) or "."]
    seen = 0
    while pending:
        current = pending.pop()
        try:
            entries = files.list_dir(root, current)
        except (NotADirectoryError, ValueError):
            # A file path can be searched directly. upload_info also rejects links/devices.
            files.upload_info(root, current)
            seen += 1
            if seen <= MAX_SCAN_FILES:
                yield current
            continue
        for item in reversed(entries):
            if item["is_dir"]:
                if item["name"] not in SKIP_DIRS:
                    pending.append(item["path"])
            else:
                seen += 1
                if seen > MAX_SCAN_FILES:
                    return
                yield item["path"]


def glob_files(root: str, args: GlobFiles) -> dict:
    found = [rel for rel in _walk(root, args.path)
             if fnmatch.fnmatch(rel, args.pattern) or fnmatch.fnmatch(Path(rel).name, args.pattern)]
    found.sort()
    page = found[args.offset:args.offset + args.limit]
    return {"files": page, "count": len(page), "total": len(found),
            "offset": args.offset, "truncated": args.offset + len(page) < len(found)}


def _read_searchable(root: str, rel: str) -> str | None:
    try:
        info = files.upload_info(root, rel)
        if info["size"] > min(MAX_SEARCH_BYTES, files.MAX_FILE_BYTES):
            return None
        return files.read_file(root, rel)["content"]
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def grep_files(root: str, args: GrepFiles) -> dict:
    flags = re.IGNORECASE if args.case_insensitive else 0
    pattern = re.compile(args.pattern, flags)
    rows: list[dict] = []
    matched_files = 0
    for rel in _walk(root, args.path):
        if args.glob and not (fnmatch.fnmatch(rel, args.glob) or fnmatch.fnmatch(Path(rel).name, args.glob)):
            continue
        text = _read_searchable(root, rel)
        if text is None:
            continue
        lines = text.splitlines()
        hits = [i for i, line in enumerate(lines) if pattern.search(line)]
        if not hits:
            continue
        matched_files += 1
        if args.output_mode == "files_with_matches":
            rows.append({"path": rel})
        elif args.output_mode == "count":
            rows.append({"path": rel, "count": len(hits)})
        else:
            for index in hits:
                start, end = max(0, index - args.context), min(len(lines), index + args.context + 1)
                rows.append({"path": rel, "line": index + 1,
                             "text": "\n".join(f"{n + 1}:{lines[n]}" for n in range(start, end))})
    page = rows[args.offset:args.offset + args.head_limit]
    result = {"mode": args.output_mode, "matches": page, "matched_files": matched_files,
              "total": len(rows), "offset": args.offset,
              "truncated": args.offset + len(page) < len(rows)}
    # A second bound protects against a handful of very long source lines.
    while len(str(result)) > MAX_RESULT_CHARS and page:
        page.pop()
        result["matches"] = page
        result["truncated"] = True
    return result


_SYMBOLS = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|interface|type|enum|struct|trait|fn)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_IMPORTANT = {"readme.md", "layla.md", "package.json", "pyproject.toml", "cargo.toml", "go.mod",
              "dockerfile", "docker-compose.yml", "compose.yml", "makefile", "requirements.txt"}


def repo_map(root: str, args: RepoMap) -> dict:
    entries = []
    base_parts = len(files._parts(args.path))
    for rel in _walk(root, args.path):
        depth = len(Path(rel).parts) - base_parts
        if depth > args.depth:
            continue
        text = _read_searchable(root, rel)
        symbols = _SYMBOLS.findall(text or "")[:20]
        try:
            size = files.upload_info(root, rel)["size"]
        except (OSError, ValueError):
            continue
        entries.append({"path": rel, "size": size, "symbols": symbols})
    entries.sort(key=lambda item: (Path(item["path"]).name.lower() not in _IMPORTANT,
                                   len(Path(item["path"]).parts), item["path"].lower()))
    truncated = len(entries) > args.max_files
    return {"files": entries[:args.max_files], "count": min(len(entries), args.max_files),
            "truncated": truncated}


def list_skills(root: str, _args: ListSkills) -> dict:
    try:
        entries = files.list_dir(root, ".layla/skills")
    except FileNotFoundError:
        entries = []
    return {"skills": [Path(item["name"]).stem for item in entries
                       if not item["is_dir"] and item["name"].endswith(".md")]}


def read_skill(root: str, args: ReadSkill) -> dict:
    result = files.read_file(root, f".layla/skills/{args.name}.md")
    return {"name": args.name, "content": result["content"]}


def execute(root: str, name: str, arguments: dict) -> dict:
    schema = SCHEMAS[name]
    args = schema.model_validate(arguments)
    return globals()[name](root, args)
