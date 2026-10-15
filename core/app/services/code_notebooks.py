"""Jupyter notebook tools using the same hashes and file boundary as the editor."""
from __future__ import annotations

import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.services import files


class NotebookRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    include_outputs: bool = False


class NotebookEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    action: Literal["replace", "insert", "delete"] = "replace"
    cell_index: int = Field(ge=0)
    cell_type: Literal["code", "markdown", "raw"] = "code"
    source: str = Field(default="", max_length=400_000)


SCHEMAS = {"notebook_read": NotebookRead, "notebook_edit": NotebookEdit}
DESCRIPTIONS = {
    "notebook_read": "Read cells of a Jupyter .ipynb notebook with stable indices, IDs and a version hash. Outputs are omitted unless requested; results are bounded and pageable.",
    "notebook_edit": "Insert, replace or delete one Jupyter notebook cell by zero-based index. Requires the version hash from notebook_read. Clears stale execution results when a code cell changes, preserves other cells/metadata, and returns a diff. Does not execute code.",
}
TOOLS = [{"type": "function", "function": {
    "name": name, "description": DESCRIPTIONS[name], "parameters": model.model_json_schema(),
}} for name, model in SCHEMAS.items()]


def load(root: str, path: str) -> tuple[dict, str]:
    if not path.endswith(".ipynb"):
        raise ValueError("Укажите файл .ipynb")
    current = files.read_file(root, path)
    try:
        doc = json.loads(current["content"])
    except json.JSONDecodeError as exc:
        raise ValueError("Некорректный JSON notebook") from exc
    if not isinstance(doc, dict) or doc.get("nbformat") != 4 or not isinstance(doc.get("cells"), list):
        raise ValueError("Поддерживаются notebook формата nbformat 4")
    for cell in doc["cells"]:
        if not isinstance(cell, dict) or cell.get("cell_type") not in ("code", "markdown", "raw"):
            raise ValueError("Некорректная ячейка notebook")
        source = cell.get("source", "")
        if not isinstance(source, str) and not (
            isinstance(source, list) and all(isinstance(line, str) for line in source)
        ):
            raise ValueError("Некорректный исходный текст ячейки")
    return doc, current["sha256"]


def new_content(root: str, args: NotebookEdit) -> tuple[str, str]:
    doc, version = load(root, args.path)
    if version != args.expected_sha256:
        raise files.FileConflict("Notebook изменился. Прочитайте его заново перед сохранением.")
    cells = doc["cells"]
    if args.cell_index > len(cells) or (args.action != "insert" and args.cell_index == len(cells)):
        raise ValueError("Ячейка с таким индексом не найдена")
    if args.action == "delete":
        del cells[args.cell_index]
    else:
        original = cells[args.cell_index] if args.action == "replace" else {}
        cell = {**original, "cell_type": args.cell_type,
                "source": args.source.splitlines(keepends=True),
                "metadata": original.get("metadata", {}), "id": original.get("id") or uuid4().hex[:12]}
        if args.cell_type == "code":
            cell.update(execution_count=None, outputs=[])
            cell.pop("attachments", None)
        else:
            cell.pop("execution_count", None)
            cell.pop("outputs", None)
            if args.cell_type != "markdown":
                cell.pop("attachments", None)
        if args.action == "insert":
            cells.insert(args.cell_index, cell)
        else:
            cells[args.cell_index] = cell
        doc["nbformat_minor"] = max(5, int(doc.get("nbformat_minor", 0)))
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n", version


def execute(root: str, name: str, arguments: dict) -> dict:
    args = SCHEMAS[name].model_validate(arguments)
    if name == "notebook_edit":
        content, version = new_content(root, args)
        return {"change": files.change_file(root, args.path, content, version)}
    doc, version = load(root, args.path)
    result, chars = [], 0
    for index in range(args.offset, min(len(doc["cells"]), args.offset + args.limit)):
        cell = doc["cells"][index]
        source = cell.get("source", "")
        text = source if isinstance(source, str) else "".join(source)
        item = {"index": index, "id": cell.get("id"), "cell_type": cell["cell_type"],
                "source": text[:12000], "source_truncated": len(text) > 12000}
        if args.include_outputs:
            item["outputs"] = json.dumps(cell.get("outputs", []), ensure_ascii=False)[:8000]
            item["execution_count"] = cell.get("execution_count")
        if chars + len(json.dumps(item, ensure_ascii=False)) > 50000:
            break
        chars += len(json.dumps(item, ensure_ascii=False))
        result.append(item)
    return {"path": args.path, "sha256": version, "cells": result, "offset": args.offset,
            "total_cells": len(doc["cells"]), "truncated": args.offset + len(result) < len(doc["cells"])}
