"""Применить изменения файлов проекта к рабочей копии. Запускается ОТ ИМЕНИ пользователя песочницы.

Отдельный самодостаточный скрипт (python -I apply.py <workspace>): всё, что он делает с
файлами, делается с правами владельца рабочей копии. Поэтому символические ссылки, которые
мог оставить код пользователя, не выводят запись за пределы его собственных файлов.

stdin: первая строка — JSON {"mkdirs": [...], "remove": [...]}, дальше — tar с файлами.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath


def parts(rel: str) -> tuple[str, ...]:
    path = PurePosixPath(rel)
    items = path.parts
    if (not rel or path.is_absolute() or len(items) > 64 or "\x00" in rel
            or any(p in ("", ".", "..") for p in items)):
        raise ValueError(f"недопустимый путь: {rel!r}")
    return items


def _clear(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def write(root: Path, rel: str, data, mode: int) -> None:
    items = parts(rel)
    target = root
    for part in items[:-1]:
        target = target / part
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            target.unlink()
        if not target.exists():
            target.mkdir()
    final = target / items[-1]
    if final.exists() or final.is_symlink():
        _clear(final)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(final, flags, 0o755 if mode & 0o111 else 0o644)
    with os.fdopen(fd, "wb") as out:
        shutil.copyfileobj(data, out)


def remove(root: Path, rel: str) -> None:
    try:
        items = parts(rel)
    except ValueError:
        return
    target = root
    for part in items[:-1]:
        target = target / part
        if target.is_symlink() or not target.is_dir():
            return
    final = target / items[-1]
    if final.is_symlink() or final.is_file():
        final.unlink()
    # Опустевшие каталоги проекта убираем, чтобы копия совпадала с проектом.
    parent = final.parent
    while parent != root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def main() -> int:
    root = Path(sys.argv[1])
    stream = sys.stdin.buffer
    header = json.loads(stream.readline() or b"{}")
    for directory in header.get("mkdirs", []):
        os.makedirs(directory, mode=0o700, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    for rel in header.get("remove", []):
        remove(root, rel)
    written = 0
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            if not member.isfile():
                continue
            data = archive.extractfile(member)
            if data is None:
                continue
            write(root, member.name, data, member.mode)
            written += 1
    print(json.dumps({"written": written}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — причина уходит серверу в stderr
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
