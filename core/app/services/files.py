"""Project-scoped file operations. All I/O uses no-follow directory descriptors.

The lexical/containment check is shared by reads and writes. Descriptor walking
also prevents a symlink swapped in after validation from redirecting an operation.
Linux flock serializes Layla writers (including separate workers); hashes prevent
stale editor/agent writes. No operation executes project code.
"""

from __future__ import annotations

import difflib
import errno
import fcntl
import hashlib
import os
import stat
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath

MAX_FILE_BYTES = 1_000_000
MAX_ZIP_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 10_000
_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".next"}
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class FileConflict(ValueError):
    """The file differs from the version the caller read."""


def _parts(rel: str) -> tuple[str, ...]:
    if (
        not isinstance(rel, str)
        or len(rel) > 4096
        or any(ord(c) < 32 for c in rel)
        or "\\" in rel
        or Path(rel).is_absolute()
        or PureWindowsPath(rel).drive
        or ".." in rel.split("/")
    ):
        raise ValueError("Недопустимый относительный путь")
    parts = Path(rel or ".").parts
    if ".git" in parts or len(parts) > 64:
        raise ValueError("Недопустимый путь проекта")
    return parts


def safe_join(base: str | Path, rel: str) -> Path:
    """Validate a relative project path; never accept absolute paths or links."""
    parts = _parts(rel)
    root = Path(base).absolute()
    if root.is_symlink():
        raise ValueError("Символические ссылки недоступны")
    target = root
    for part in parts:
        target /= part
        if target.is_symlink():
            raise ValueError("Символические ссылки недоступны")
    resolved = target.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("Путь вне каталога проекта")
    return target


@contextmanager
def _root(base: str | Path, *, write: bool = False):
    fd = os.open(base, _DIR_FLAGS)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _directory(root: int, parts: tuple[str, ...], *, create: bool = False):
    fd = os.dup(root)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError("Каталог недоступен или содержит символическую ссылку") from exc
        raise
    finally:
        os.close(fd)


def _read_bytes(parent: int, name: str, limit: int) -> tuple[bytes, os.stat_result]:
    # O_NONBLOCK prevents FIFOs/devices from hanging before fstat can reject them.
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Доступны только обычные файлы без ссылок")
        if info.st_size > limit:
            raise ValueError("Файл превышает допустимый размер")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Файл превышает допустимый размер")
        return data, info


def _text(data: bytes) -> str:
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Редактор поддерживает только текст UTF-8") from exc
    if "\0" in content:
        raise ValueError("Бинарные файлы недоступны в редакторе")
    return content


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def list_dir(base: str | Path, rel: str = ".") -> list[dict]:
    safe_join(base, rel)
    with _root(base) as root, _directory(root, _parts(rel)) as fd:
        entries = []
        for name in os.listdir(fd):
            if name in _SKIP_DIRS:
                continue
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                continue
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                continue
            entries.append(
                {"name": name, "path": str(Path(rel) / name), "is_dir": stat.S_ISDIR(info.st_mode)}
            )
        return sorted(entries, key=lambda e: (not e["is_dir"], e["name"].lower()))


def read_file(base: str | Path, rel: str) -> dict:
    safe_join(base, rel)
    parts = _parts(rel)
    if not parts:
        raise ValueError("Укажите путь файла")
    with _root(base) as root, _directory(root, parts[:-1]) as parent:
        data, _ = _read_bytes(parent, parts[-1], MAX_FILE_BYTES)
    return {"path": "/".join(parts), "content": _text(data), "sha256": _sha(data)}


def read_text(base: str | Path, rel: str) -> str:
    return read_file(base, rel)["content"]


def _change(path: str, before: str | None, after: str | None) -> dict:
    operation = "create" if before is None else "delete" if after is None else "edit"
    lines = difflib.unified_diff(
        (before or "").splitlines(keepends=True),
        (after or "").splitlines(keepends=True),
        fromfile=f"a/{path}" if before is not None else "/dev/null",
        tofile=f"b/{path}" if after is not None else "/dev/null",
    )
    diff = "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines
    )
    return {
        "path": path,
        "operation": operation,
        "diff": diff,
        "before_sha256": _sha(before.encode()) if before is not None else None,
        "after_sha256": _sha(after.encode()) if after is not None else None,
    }


def preview_change(base: str | Path, rel: str, content: str | None) -> dict:
    """Diff, который дал бы change_file, — без записи на диск (для подтверждения)."""
    safe_join(base, rel)
    parts = _parts(rel)
    if not parts:
        raise ValueError("Укажите путь файла")
    try:
        before = read_file(base, rel)["content"]
    except FileNotFoundError:
        before = None
    return _change("/".join(parts), before, content)


def change_file(
    base: str | Path,
    rel: str,
    content: str | None,
    expected_sha256: str | None,
) -> dict:
    """Create/edit/delete one text file. None content deletes; None hash creates.

    A create never overwrites. Edits/deletes require the SHA returned by read_file.
    Parents are created automatically. Directories cannot be deleted.
    """
    safe_join(base, rel)
    parts = _parts(rel)
    if not parts:
        raise ValueError("Укажите путь файла")
    data = content.encode("utf-8") if content is not None else None
    if data is not None:
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("Файл превышает допустимый размер (1 МБ)")
        _text(data)
    with (
        _root(base, write=True) as root,
        _directory(
            root,
            parts[:-1],
            create=data is not None and expected_sha256 is None,
        ) as parent,
    ):
        try:
            old, info = _read_bytes(parent, parts[-1], MAX_FILE_BYTES)
        except FileNotFoundError:
            old, info = None, None
        if (old is None and expected_sha256 is not None) or (
            old is not None and _sha(old) != expected_sha256
        ):
            raise FileConflict("Файл изменился. Прочитайте его заново перед сохранением.")
        before = _text(old) if old is not None else None
        if data is None:
            if old is None:
                raise FileNotFoundError(rel)
            os.unlink(parts[-1], dir_fd=parent)
        else:
            # Same-directory replace avoids partial files and never follows a target link.
            temp = f".layla-write-{uuid.uuid4().hex}"
            try:
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fchmod(
                        stream.fileno(), stat.S_IMODE(info.st_mode) & 0o777 if info else 0o644
                    )
                    os.fsync(stream.fileno())
                if old is None:
                    # Atomic no-clobber publication, also against external creators.
                    os.link(
                        temp, parts[-1], src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False
                    )
                    os.unlink(temp, dir_fd=parent)
                else:
                    os.replace(temp, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
            finally:
                try:
                    os.unlink(temp, dir_fd=parent)
                except FileNotFoundError:
                    pass
        os.fsync(parent)
    return _change("/".join(parts), before, content)


def export_zip(base: str | Path):
    """Build a bounded ZIP in a spooled temp file; caller must close it.

    Skip links, special files, git metadata, dependency/build caches. Files are
    opened relative to the same locked root, including during recursive traversal.
    """
    safe_join(base, ".")
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)  # noqa: SIM115 — response owns it
    total = count = 0
    try:
        with _root(base) as root, zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as archive:

            def visit(fd: int, prefix: str = "", depth: int = 0):
                nonlocal total, count
                if depth > 64:
                    raise ValueError("Слишком большая глубина каталогов для ZIP")
                for name in sorted(os.listdir(fd)):
                    if name in _SKIP_DIRS or name.startswith(".layla-write-"):
                        continue
                    path = prefix + name
                    _parts(path)
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        continue
                    count += 1
                    if count > MAX_ZIP_ENTRIES:
                        raise ValueError("В ZIP допускается не более 10000 файлов и каталогов")
                    if stat.S_ISDIR(info.st_mode):
                        with _directory(fd, (name,)) as child:
                            archive.writestr(path + "/", b"")
                            visit(child, path + "/", depth + 1)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        data, opened = _read_bytes(fd, name, MAX_ZIP_BYTES - total)
                        total += len(data)
                        member = zipfile.ZipInfo(path)
                        member.external_attr = (stat.S_IFREG | (opened.st_mode & 0o777)) << 16
                        member.compress_type = zipfile.ZIP_DEFLATED
                        archive.writestr(member, data)

            visit(root)
        spool.seek(0)
        return spool
    except BaseException:
        spool.close()
        raise
