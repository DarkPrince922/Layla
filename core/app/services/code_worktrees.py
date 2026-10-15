"""Private Git worktrees built from the actual project snapshot, not its HEAD.

The source repository and its index are never changed. Each worker owns a tiny
private snapshot repository, so this also works for projects without Git. Model
arguments cannot specify server paths, refs, repositories or Git configuration.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from pathlib import Path
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from app.services import files, gitops

MAX_CHANGED_FILES = 200
MAX_CHANGE_BYTES = 32 * 1024 * 1024


def paths(root: str, project_id: str, worker_id: str) -> tuple[Path, Path, Path]:
    project_id, worker_id = str(UUID(project_id)), str(UUID(worker_id))
    base = Path(root).absolute().parent / ".layla-code-workers"
    location = base / project_id / worker_id
    # All parents are application-owned. Reject even a contained symlink so a
    # directory swap cannot intentionally redirect the workspace to another job.
    current = base
    for part in ("", project_id, worker_id):
        if part:
            current /= part
        if current.is_symlink():
            raise ValueError("Каталог рабочих копий не может быть ссылкой")
    return location, location / "repository", location / "worktree"


def _snapshot(root: str, location: Path, repository: Path) -> dict:
    location.mkdir(parents=True, exist_ok=False, mode=0o700)
    repository.mkdir(mode=0o700)
    baseline = {}
    # export_zip holds the source root lock while copying; no mixed snapshot of
    # concurrent Layla editor writes, links, devices, .git or dependency caches.
    with files.export_zip(root) as buffer, zipfile.ZipFile(buffer) as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            data = archive.read(item)
            files.change_file(repository, item.filename, data, None)
            os.chmod(repository / item.filename, item.external_attr >> 16 & 0o777)
            baseline[item.filename] = hashlib.sha256(data).hexdigest()
    return baseline


async def create(root: str, project_id: str, worker_id: str) -> dict:
    location, repository, worktree = paths(root, project_id, worker_id)
    try:
        baseline = await run_in_threadpool(_snapshot, root, location, repository)
        await gitops._git(str(repository), "init", "-q", "-b", "snapshot")
        (repository / ".git/info/attributes").write_text("* -text -filter -ident -working-tree-encoding\n")
        # Include ignored/untracked files from the snapshot. No repository hooks,
        # remotes, filters, credentials or user Git config are copied.
        await gitops._git(str(repository), "add", "--all", "--force", "--", ".")
        await gitops._git(str(repository), "commit", "--allow-empty", "-q", "-m", "Code worker snapshot",
                          config={"user.name": "Layla", "user.email": "worker@localhost"})
        await gitops._git(str(repository), "worktree", "add", "--detach", str(worktree), "HEAD")
        # Git records only the executable bit; preserve the snapshot's remaining permission bits too.
        for rel in baseline:
            os.chmod(files.safe_join(worktree, rel), os.stat(files.safe_join(repository, rel)).st_mode & 0o777)
        return {"baseline": baseline, "initialized": True}
    except BaseException:
        # This directory was generated for this worker only. Never delete the
        # source root or a directory named by model arguments.
        await run_in_threadpool(shutil.rmtree, location, True)
        raise


def diff(root: str, project_id: str, worker_id: str, baseline: dict[str, str]) -> list[dict]:
    _, _, worktree = paths(root, project_id, worker_id)
    if not worktree.is_dir():
        raise ValueError("Рабочая копия не найдена; выполненные правки не восстановить из основного проекта")
    current = {}
    pending = ["."]
    while pending:
        for item in files.list_dir(worktree, pending.pop()):
            if item["is_dir"]:
                pending.append(item["path"])
            else:
                current[item["path"]] = files.upload_info(worktree, item["path"])["sha256"]
                if len(current) > files.MAX_ZIP_ENTRIES:
                    raise ValueError("Рабочая копия содержит слишком много файлов")
    changed = []
    total = 0
    for rel in sorted(set(baseline) | set(current)):
        files.safe_join(worktree, rel)  # reject a replacement symlink, never import it as deletion
        repository = paths(root, project_id, worker_id)[1]
        before = files.read_blob(repository, rel) if baseline.get(rel) is not None else None
        after = files.read_blob(worktree, rel) if current.get(rel) is not None else None
        if baseline.get(rel) == current.get(rel) and (before is None or after is None or before["mode"] == after["mode"]):
            continue
        if len(changed) >= MAX_CHANGED_FILES:
            raise ValueError("Воркер изменил больше 200 файлов: разбейте задачу")
        total += len(after["data"] if after else b"") + len(before["data"] if before else b"")
        if total > MAX_CHANGE_BYTES:
            raise ValueError("Изменения воркера превышают 32 МиБ: разбейте задачу")
        change = files.blob_change(rel, before["data"] if before else None, after["data"] if after else None)
        change.update(before_mode=before["mode"] if before else None, mode=after["mode"] if after else None)
        if before and after and before["mode"] != after["mode"]:
            change["diff"] = f"mode {oct(before['mode'])} → {oct(after['mode'])}\n" + change["diff"]
        changed.append(change)
    return changed


def proposal(root: str, project_id: str, worker_id: str, change: dict) -> dict:
    _, _, worktree = paths(root, project_id, worker_id)
    rel = change["path"]
    try:
        info = files.read_blob(root, rel)
    except FileNotFoundError:
        info = {"sha256": None, "mode": None}
    expected = change["before_sha256"]
    if info["sha256"] == change["after_sha256"] and (change.get("mode") is None or info["mode"] == change["mode"]):
        return {"path": rel, "already_applied": True}
    if info["sha256"] != expected or change.get("before_mode") is not None and info["mode"] != change["before_mode"]:
        raise files.FileConflict(f"{rel}: основной файл изменился после запуска воркера. Изменения сохранены в рабочей копии.")
    content = None
    if change["after_sha256"] is not None:
        current = files.read_blob(worktree, rel)
        if current["sha256"] != change["after_sha256"]:
            raise files.FileConflict(f"{rel}: рабочая копия изменилась после завершения воркера")
        content = current["data"]
    return {"path": rel, "content": content, "expected_sha256": expected, "expected_mode": info["mode"],
            "mode": change.get("mode"), "change": change}


async def remove(root: str, project_id: str, worker_id: str) -> None:
    location, _, _ = paths(root, project_id, worker_id)
    await run_in_threadpool(shutil.rmtree, location, True)


def advance_baseline(root: str, project_id: str, worker_id: str, rel: str, sha: str | None):
    """Keep imported versions as the next resume's baseline, including reversions."""
    _, repository, worktree = paths(root, project_id, worker_id)
    content = None
    if sha is not None:
        current = files.read_blob(worktree, rel)
        if current["sha256"] != sha:
            raise files.FileConflict("Рабочая копия изменилась во время импорта")
        content = current["data"]
    old = files.upload_info(repository, rel)["sha256"]
    if old != sha or sha is not None:
        files.change_file(repository, rel, content, old, binary=True, mode=current["mode"] if sha is not None else None)


def public_change(change: dict) -> dict:
    return {key: value for key, value in change.items() if key != "diff"}
