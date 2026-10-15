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
MAX_CHANGE_BYTES = 4_000_000


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
            os.chmod(repository / item.filename, 0o755 if item.external_attr >> 16 & 0o111 else 0o644)
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
        if baseline.get(rel) == current.get(rel):
            continue
        files.safe_join(worktree, rel)  # reject a replacement symlink, never import it as deletion
        if len(changed) >= MAX_CHANGED_FILES:
            raise ValueError("Воркер изменил больше 200 файлов: разбейте задачу")
        # Text changes only. Binary inputs stay in the snapshot but are never
        # imported through a lossy decode or silently treated as deletions.
        before = None
        repository = paths(root, project_id, worker_id)[1]
        if baseline.get(rel) is not None:
            before = files.read_file(repository, rel)["content"]
        after = files.read_file(worktree, rel)["content"] if current.get(rel) is not None else None
        total += len((after or "").encode()) + len((before or "").encode())
        if total > MAX_CHANGE_BYTES:
            raise ValueError("Изменения воркера превышают 4 МБ: разбейте задачу")
        change = files._change(rel, before, after)
        changed.append(change)
    return changed


def proposal(root: str, project_id: str, worker_id: str, change: dict) -> dict:
    _, _, worktree = paths(root, project_id, worker_id)
    rel = change["path"]
    info = files.upload_info(root, rel)
    expected = change["before_sha256"]
    if info["sha256"] == change["after_sha256"]:
        return {"path": rel, "already_applied": True}
    if info["sha256"] != expected:
        raise files.FileConflict(f"{rel}: основной файл изменился после запуска воркера. Изменения сохранены в рабочей копии.")
    content = None
    if change["after_sha256"] is not None:
        current = files.read_file(worktree, rel)
        if current["sha256"] != change["after_sha256"]:
            raise files.FileConflict(f"{rel}: рабочая копия изменилась после завершения воркера")
        content = current["content"]
    return {"path": rel, "content": content, "expected_sha256": expected}


async def remove(root: str, project_id: str, worker_id: str) -> None:
    location, _, _ = paths(root, project_id, worker_id)
    await run_in_threadpool(shutil.rmtree, location, True)


def advance_baseline(root: str, project_id: str, worker_id: str, rel: str, sha: str | None):
    """Keep imported versions as the next resume's baseline, including reversions."""
    _, repository, worktree = paths(root, project_id, worker_id)
    content = None
    if sha is not None:
        current = files.read_file(worktree, rel)
        if current["sha256"] != sha:
            raise files.FileConflict("Рабочая копия изменилась во время импорта")
        content = current["content"]
    old = files.upload_info(repository, rel)["sha256"]
    if old != sha:
        files.change_file(repository, rel, content, old)


def public_change(change: dict) -> dict:
    return {key: value for key, value in change.items() if key != "diff"}
