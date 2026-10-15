"""Read-only Code intelligence through the sandbox's managed language servers."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.services import files, sandbox


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["goToDefinition", "findReferences", "hover", "documentSymbol", "workspaceSymbol",
                       "goToImplementation", "prepareCallHierarchy", "incomingCalls", "outgoingCalls", "diagnostics"]
    path: str = Field(min_length=1, max_length=4096)
    line: int = Field(default=1, ge=1, le=1_000_000)
    character: int = Field(default=1, ge=1, le=1_000_001)
    query: str = Field(default="", max_length=200)


class EditorQuery(Query):
    content: str | None = Field(default=None, max_length=1_000_000)


TOOL = {"type": "function", "function": {
    "name": "lsp", "description": (
        "Read-only language-server analysis of saved project code: goToDefinition, findReferences, hover, "
        "documentSymbol, workspaceSymbol (query), goToImplementation, prepareCallHierarchy, incomingCalls, "
        "outgoingCalls and diagnostics. path is relative to this project; input line/character are 1-based "
        "UTF-16 offsets, output ranges are 0-based UTF-16. Follow returned project paths with read_file. "
        "Use definitions/references before broad text search and diagnostics after changes. Server capabilities "
        "vary; unsupported operations return an error, never a fabricated empty result. External/dependency "
        "locations are filtered. Diagnostics ready=false means analysis is still pending, not a clean file. "
        "No code, shell command or workspace edit is executed on the API host."
    ), "parameters": Query.model_json_schema(),
}}
SUPPORTED = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}


def validate_position(text: str, args: Query):
    if args.operation in {"documentSymbol", "workspaceSymbol", "diagnostics"}:
        return
    lines = text.split("\n")
    if args.line > len(lines) or args.character > len(lines[args.line - 1].rstrip("\r").encode("utf-16-le")) // 2 + 1:
        raise ValueError("Позиция находится за пределами файла (строки и UTF-16 колонки от 1)")


def filter_paths(root: str, value):
    """Recheck every returned location against the authoritative project, no links."""
    if isinstance(value, list):
        return [item for entry in value if (item := filter_paths(root, entry)) is not None]
    if isinstance(value, dict):
        if "path" in value:
            try:
                if files.upload_info(root, value["path"])["sha256"] is None:
                    return None
            except (ValueError, OSError, TypeError):
                return None
        return {key: filter_paths(root, entry) for key, entry in value.items()}
    return value


def utf16_ranges(root: str, value, path: str, texts: dict[str, str]):
    """Adapt legacy pylsp codepoint ranges using authoritative no-follow text."""
    if isinstance(value, list):
        return [utf16_ranges(root, item, path, texts) for item in value]
    if not isinstance(value, dict):
        return value
    path = value.get("path", path)
    if isinstance(value.get("line"), int) and isinstance(value.get("character"), int):
        if path not in texts:
            if len(texts) >= 32:
                return None
            try:
                texts[path] = files.read_file(root, path)["content"]
            except (ValueError, OSError):
                return None
        lines = texts[path].split("\n")
        line, character = value["line"], value["character"]
        if not 0 <= line < len(lines) or character < 0:
            return None
        return {**value, "character": len(lines[line][:character].encode("utf-16-le")) // 2}
    return {key: utf16_ranges(root, item, path, texts) for key, item in value.items()}


class Client:
    def __init__(self, owner_id: str, project_id: str, root: str):
        self.owner_id, self.project_id, self.root = owner_id, project_id, root

    async def tools(self, permissions=None):
        if permissions is not None and "files.read" not in permissions:
            return []
        info = await sandbox.info()
        return [TOOL] if info is not None and info.get("lsp") else []

    async def call(self, args: Query, content: str | None = None):
        saved = await run_in_threadpool(files.read_file, self.root, args.path)
        if Path(args.path).suffix.lower() not in SUPPORTED:
            raise ValueError("Для расширения этого файла LSP не настроен")
        text = saved["content"] if content is None else content
        if "\x00" in text or len(text.encode()) > files.MAX_FILE_BYTES:
            raise ValueError("LSP принимает текст UTF-8 до 1 МБ без NUL")
        validate_position(text, args)
        await sandbox.sync(self.owner_id, self.project_id, self.root)
        result = await self.transport("POST", {**args.model_dump(), "text": text})
        result["result"] = await run_in_threadpool(filter_paths, self.root, result.get("result"))
        if result.get("position_encoding") == "utf-32":
            result["result"] = await run_in_threadpool(utf16_ranges, self.root, result["result"], args.path, {args.path: text})
            result["position_encoding"] = "utf-16"
        result["source_sha256"] = saved["sha256"]
        result["content_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        result["unsaved"] = text != saved["content"]
        current = await run_in_threadpool(files.upload_info, self.root, args.path)
        result["stale_source"] = current["sha256"] != saved["sha256"]
        return result

    async def transport(self, method: str, payload=None):
        url = f"/workspaces/{sandbox._owner_key(self.owner_id)}/{self.project_id}/lsp"
        try:
            async with sandbox._client(httpx.Timeout(90, connect=5)) as client:
                response = await client.request(method, url, json=payload)
                if response.status_code >= 400:
                    raise sandbox.SandboxError(sandbox._detail(response, "LSP недоступен; пересоберите sandbox"))
                return response.json()
        except httpx.HTTPError as exc:
            raise sandbox.SandboxError("Ошибка соединения с LSP песочницы; повторите запрос") from exc
