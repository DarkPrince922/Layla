"""Checked import of sandbox command results. No project code executes in Core."""
from __future__ import annotations

import base64
import re

from starlette.concurrency import run_in_threadpool

from app.services import files, sandbox

MAX_TOTAL = 32 * 1024 * 1024
SHA = re.compile(r"^[0-9a-f]{64}$")
_UNSET = object()


def checkpoint(root: str, path: str):
    try:
        item = files.read_blob(root, path)
    except FileNotFoundError:
        return {"path": path, "content": None}
    try:
        content = files._text(item["data"])
        if len(item["data"]) > files.MAX_FILE_BYTES:
            raise ValueError("Large text uses the binary rollback representation")
        encoding = "utf-8"
    except ValueError:
        content, encoding = base64.b64encode(item["data"]).decode("ascii"), "base64"
    return {"path": path, "content": content, "encoding": encoding, "mode": item["mode"]}


def restore(root, point, *, expected_sha256=_UNSET, expected_mode=None):
    path = point["path"]
    try:
        old = files.read_blob(root, path)
    except FileNotFoundError:
        old = {"sha256": None}
    if expected_sha256 is not _UNSET and old["sha256"] != expected_sha256:
        raise files.FileConflict(path + ": файл изменился во время отката")
    content = point["content"]
    if content is not None:
        content = base64.b64decode(content, validate=True) if point.get("encoding") == "base64" else content.encode()
    if old["sha256"] is not None or content is not None:
        return files.change_file(root, path, content, old["sha256"], binary=True, mode=point.get("mode"), expected_mode=expected_mode)


def prepare(root: str, packet: dict) -> list[dict]:
    if packet.get("error"):
        raise ValueError("Не удалось собрать изменения команды: " + str(packet["error"])[:1000])
    changes = packet.get("changes", [])
    if not isinstance(changes, list) or len(changes) > 200:
        raise ValueError("Некорректный пакет изменений shell")
    proposals, seen, total = [], set(), 0
    for change in changes:
        path = change["path"]
        files.safe_join(root, path)
        if path in seen or not files._parts(path):
            raise ValueError("Повторяющийся или пустой путь shell")
        seen.add(path)
        before, after = change["before_sha256"], change["after_sha256"]
        if any(sha is not None and (not isinstance(sha, str) or not SHA.fullmatch(sha)) for sha in (before, after)):
            raise ValueError("Некорректный хеш shell")
        mode = change.get("mode")
        if mode is not None and (type(mode) is not int or not 0 <= mode <= 0o777):
            raise ValueError("Недопустимый режим файла shell")
        data = change.get("data")
        if after is None:
            if data is not None:
                raise ValueError("Удаление содержит данные")
        else:
            if not isinstance(data, str) or len(data) > files.MAX_UPLOAD_BYTES * 4 // 3 + 4:
                raise ValueError("Недопустимый размер файла shell")
            data = base64.b64decode(data, validate=True)
            if len(data) > files.MAX_UPLOAD_BYTES or files._sha(data) != after:
                raise ValueError("Содержимое shell не соответствует хешу или превышает 10 МиБ")
        total += len(data or b"")
        if total > MAX_TOTAL:
            raise ValueError("Изменения shell превышают 32 МиБ")
        try:
            old = files.read_blob(root, path)
        except FileNotFoundError:
            old = {"sha256": None, "data": None, "mode": None}
        already = old["sha256"] == after and (after is None or mode is None or old["mode"] == mode)
        if not already and (old["sha256"] != before or
                            change.get("before_mode") is not None and old["mode"] != change["before_mode"]):
            raise files.FileConflict(f"{path}: файл изменился параллельно команде. Результат сохранён в command_changes.")
        diff = files.blob_change(path, old["data"], data)
        if mode is not None and old["mode"] != mode:
            diff["diff"] = f"mode {oct(old['mode'] or 0)} → {oct(mode)}\n" + diff["diff"]
        proposals.append({"path": path, "data": data, "expected_sha256": old["sha256"],
                          "expected_mode": old["mode"], "mode": mode, "change": diff, "already_applied": already})
    return proposals


async def fetch(runner):
    return await sandbox.shell_request(runner.owner_id, runner.project_id, "GET", "changes")


def describe(packet, root=None):
    result = {key: packet[key] for key in ("id", "status", "error", "blocked", "exit") if key in packet}
    if packet.get("output"):
        result["output"] = sandbox.squeeze(sandbox.clean(packet["output"]), 8000)[0]
    budget = 12000
    result["changes"] = []
    for item in packet.get("changes", []):
        public = {key: item.get(key) for key in ("path", "before_sha256", "after_sha256", "mode")}
        data = item.get("data")
        blob = None
        if data is not None:
            blob = base64.b64decode(data, validate=True)
            public["size"] = len(blob)
            try:
                text = files._text(blob)
                public["proposed_content"] = text[:max(0, budget)]
                budget -= len(public["proposed_content"])
            except ValueError:
                public["binary"] = True
        if root is not None:
            try:
                before = files.read_blob(root, item["path"])["data"]
            except FileNotFoundError:
                before = None
            public.update(files.blob_change(item["path"], before, blob))
            public["diff"] = public["diff"][:max(0, budget)]
            budget -= len(public["diff"])
        result["changes"].append(public)
    return result


async def acknowledge(runner, packet):
    if packet.get("id"):
        from uuid import UUID
        ident = str(UUID(packet["id"]))
        await sandbox.shell_request(runner.owner_id, runner.project_id, "DELETE", "changes/" + ident)


async def apply(runner, packet, event, *, ask=None, taken=None, writable=True):
    """Yield checkpoints before writes and actual file diffs before bookkeeping."""
    applied = []
    try:
        proposals = await run_in_threadpool(prepare, runner.root, packet)
        pending = [item for item in proposals if not item["already_applied"]]
        if pending and not writable:
            raise ValueError("Изменения сохранены в песочнице: режим/персона запрещает запись в проект")
        if pending and ask is not None:
            approval = {**event, "status": "pending", "change": {
                "path": "Изменения команды", "operation": "edit", "before_sha256": None, "after_sha256": None,
                "diff": "\n".join(item["change"]["diff"] for item in pending)[:80000]}}
            yield {"tool": approval}
            decision = await ask(approval)
            if decision == "approve_all":
                yield {"ask_off": True}
            elif decision != "approve":
                raise ValueError("Импорт отклонён. Результат сохранён в command_changes; можно применить или явно удалить пакет.")
        for item in pending:
            path = item["path"]
            if taken is None or path not in taken:
                point = await run_in_threadpool(checkpoint, runner.root, path)
                yield {"checkpoint": point}
                if taken is not None:
                    taken.add(path)
            change = await run_in_threadpool(files.change_file, runner.root, path, item["data"],
                                             item["expected_sha256"], binary=True, mode=item["mode"], expected_mode=item["expected_mode"])
            # A crash or race after an earlier file is recoverable: retry recognizes already-applied hashes.
            change["diff"] = sandbox.squeeze(item["change"]["diff"], 40000)[0]
            yield {"tool": {**event, "id": event["id"] + ":file:" + path, "name": "shell_file", "status": "done",
                            "path": path, "change": change}}
            applied.append({key: value for key, value in change.items() if key != "diff"})
        blocked = packet.get("blocked", {})
        if not blocked:
            await acknowledge(runner, packet)
        yield {"shell_result": {"applied": applied, "blocked": blocked,
                                **({"pending_changes": packet.get("id"), "note": "Некоторые пути не импортированы; проверьте и явно отклоните оставшийся пакет"} if blocked else {})}}
    except (ValueError, OSError, sandbox.SandboxError) as exc:
        yield {"shell_result": {"error": str(exc)[:1500], "applied": applied,
                                "pending_changes": packet.get("id"), "blocked": packet.get("blocked", {})}}
