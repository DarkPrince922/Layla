"""Bounded workspace export. This helper always runs as the workspace uid, never root."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import sys

SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", "target", ".gradle", ".turbo"}
MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_FILES = 20000
MAX_CHANGES = 200
FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def scan(root: str, before=None, ignored=None):
    current, blobs, blocked = {}, {}, {}
    total = scanned = entries = 0

    def walk(fd, prefix="", depth=0):
        nonlocal total, scanned, entries
        if depth > 64:
            raise ValueError("Слишком глубокая структура проекта")
        for name in sorted(os.listdir(fd)):
            if name in SKIP or name.startswith(".layla-write-"):
                continue
            entries += 1
            if entries > MAX_FILES:
                raise ValueError("Больше 20000 файлов/каталогов в проекте")
            rel = prefix + name
            if len(rel) > 4096 or "\\" in rel or any(ord(c) < 32 for c in rel):
                blocked[rel[:4096]] = "Недопустимый путь"
                continue
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, FLAGS, dir_fd=fd)
                try:
                    walk(child, rel + "/", depth + 1)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                blocked[rel] = "Ссылки и специальные файлы не импортируются"
                continue
            if info.st_size > MAX_FILE:
                blocked[rel] = "Файл больше 10 МиБ"
                continue
            opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(opened, "rb") as stream:
                first = os.fstat(stream.fileno())
                if not stat.S_ISREG(first.st_mode) or first.st_nlink != 1:
                    raise ValueError("Файл заменён ссылкой во время экспорта")
                data = stream.read(MAX_FILE + 1)
                last = os.fstat(stream.fileno())
            if len(data) > MAX_FILE or (first.st_size, first.st_mtime_ns) != (last.st_size, last.st_mtime_ns):
                blocked[rel] = "Файл изменился во время экспорта или слишком велик"
                continue
            scanned += len(data)
            if scanned > 300 * 1024 * 1024:
                raise ValueError("Снимок проекта превышает 300 МиБ")
            mark = {"sha256": hashlib.sha256(data).hexdigest(), "mode": stat.S_IMODE(last.st_mode) & 0o777}
            current[rel] = mark
            if len(current) > MAX_FILES:
                raise ValueError("Больше 20000 файлов в проекте")
            if before is not None and before.get(rel) != mark:
                total += len(data)
                if total > MAX_TOTAL or len(blobs) >= MAX_CHANGES:
                    raise ValueError("Изменения превышают 200 файлов / 32 МиБ; результаты оставлены в песочнице")
                blobs[rel] = base64.b64encode(data).decode("ascii")

    fd = os.open(root, FLAGS)
    try:
        walk(fd)
    finally:
        os.close(fd)
    if before is None:
        return {"files": current, "blocked": blocked}
    changes = []
    for rel in sorted(set(before) | set(current)):
        old, new = before.get(rel), current.get(rel)
        if old == new:
            continue
        # A replacement link/directory/oversize file is never interpreted as a deletion.
        parents = rel.split("/")
        if (rel in blocked or any("/".join(parents[:i]) in blocked for i in range(1, len(parents)))
                or new is None and os.path.lexists(os.path.join(root, rel))):
            blocked[rel] = blocked.get(rel, "Путь заменён каталогом или недоступным файлом")
            continue
        changes.append({"path": rel, "before_sha256": old["sha256"] if old else None,
                        "after_sha256": new["sha256"] if new else None,
                        "before_mode": old["mode"] if old else None, "mode": new["mode"] if new else None,
                        "data": blobs.get(rel)})
        if len(changes) > MAX_CHANGES:
            raise ValueError("Изменено больше 200 файлов; результаты оставлены в песочнице")
    return {"changes": changes, "blocked": {path: reason for path, reason in blocked.items()
                                             if path not in (ignored or []) or path in before}}


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        print(json.dumps(scan(sys.argv[1], request.get("before"), request.get("ignored"))))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
