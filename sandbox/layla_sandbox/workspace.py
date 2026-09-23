"""Рабочие копии проектов и запуск команд от имени пользователя песочницы.

Раскладка /work (том):
  /work/.state/            — только root: uid пользователей и манифесты синхронизации
  /work/u<uid>/            — владелец uid, 0700: всё, что принадлежит одному пользователю Layla
      home/                — HOME: кеши pip/npm/go/cargo общие для его проектов
      tmp/                 — TMPDIR
      projects/<project>/  — рабочая копия проекта: файлы проекта + .venv, node_modules, сборки

Сервер работает от root, но внутри каталогов пользователя ничего сам не пишет: файлы
раскладывает apply.py, запущенный от uid пользователя, команды — тоже от его uid.
"""
from __future__ import annotations

import asyncio
import codecs
import io
import json
import os
import re
import signal
import sys
import tarfile
import threading
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

from layla_sandbox import config

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
APPLY = str(Path(__file__).with_name("apply.py"))
PYTHON = sys.executable


def check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise ValueError(f"Недопустимый идентификатор: {what}")
    return value


def check_path(rel: str) -> str:
    if (not isinstance(rel, str) or not rel or len(rel) > 4096 or rel.startswith("/")
            or "\\" in rel or any(ord(c) < 32 for c in rel)):
        raise ValueError(f"Недопустимый путь: {rel!r}")
    items = rel.split("/")
    if len(items) > 64 or any(p in ("", ".", "..") for p in items):
        raise ValueError(f"Недопустимый путь: {rel!r}")
    return rel


class Users:
    """Пользователь Layla -> постоянный uid песочницы."""

    def __init__(self, work: Path) -> None:
        self.file = work / ".state" / "uids.json"
        self.lock = threading.Lock()

    def uid(self, owner: str) -> int:
        check_id(owner, "пользователь")
        with self.lock:
            try:
                known = json.loads(self.file.read_text())
            except (OSError, ValueError):
                known = {}
            if owner in known:
                return int(known[owner])
            uid = max([int(v) for v in known.values()] + [config.UID_MIN - 1]) + 1
            if uid > config.UID_MAX:
                raise RuntimeError("Закончились uid песочницы")
            known[owner] = uid
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(known))
            tmp.replace(self.file)
            return uid


def prepare_root(work: Path) -> None:
    """Корень тома: чужие каталоги нельзя даже перечислить, служебное — только root."""
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o711)
    state = work / ".state"
    (state / "manifests").mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)


class Workspace:
    def __init__(self, work: Path, uid: int, owner: str, project: str) -> None:
        self.work = work
        self.uid = uid
        self.owner = check_id(owner, "пользователь")
        self.project = check_id(project, "проект")
        self.user_dir = work / f"u{uid}"
        self.home = self.user_dir / "home"
        self.tmp = self.user_dir / "tmp"
        self.path = self.user_dir / "projects" / project
        self.manifest_file = work / ".state" / "manifests" / owner / f"{project}.json"

    # --- каталоги и манифест ---------------------------------------------------

    def ensure_user_dir(self) -> None:
        # Каталог пользователя создаёт root (в /work писать может только он) и отдаёт uid.
        if not self.user_dir.exists():
            self.user_dir.mkdir(mode=0o700)
            os.chown(self.user_dir, self.uid, self.uid)

    def load_manifest(self) -> dict[str, list]:
        try:
            data = json.loads(self.manifest_file.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_manifest(self, files: dict[str, list]) -> None:
        self.manifest_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(files))
        tmp.replace(self.manifest_file)

    def local_mark(self, rel: str) -> str | None:
        """Отпечаток файла рабочей копии: код мог его изменить или удалить."""
        try:
            st = os.lstat(self.path / rel)
        except OSError:
            return None
        return f"{st.st_size}:{st.st_mtime_ns}"

    # --- синхронизация ---------------------------------------------------------

    def plan(self, files: dict[str, str], names: set[str]) -> tuple[list[str], list[str]]:
        """(нужно прислать, удалить из копии). Файл нужен, если он новый, изменился в
        проекте или в рабочей копии (команда его изменила/удалила) и его нет в архиве."""
        stored = self.load_manifest()
        need = []
        for rel, mark in files.items():
            known = stored.get(rel)
            fresh = (isinstance(known, list) and len(known) == 2 and known[0] == mark
                     and known[1] is not None and self.local_mark(rel) == known[1])
            if not fresh and rel not in names:
                need.append(rel)
        remove = [rel for rel in stored if rel not in files]
        return need, remove

    async def apply(self, files: dict[str, str], archive: bytes, names: set[str], remove: list[str]) -> int:
        self.ensure_user_dir()
        header = {"mkdirs": [str(self.home), str(self.tmp), str(self.path)], "remove": remove}
        payload = json.dumps(header).encode() + b"\n" + (archive or empty_tar())
        proc = await asyncio.create_subprocess_exec(
            PYTHON, "-I", APPLY, str(self.path),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd="/", env={"PATH": config.BASE_PATH, "LANG": "C.UTF-8"}, **self._as_user(),
        )
        out, err = await proc.communicate(payload)
        if proc.returncode != 0:
            raise RuntimeError("Не удалось разложить файлы в песочнице: " + err.decode(errors="replace")[-500:])
        stored = self.load_manifest()
        result = {rel: stored[rel] for rel in files if rel in stored and rel not in names}
        for rel in names:
            if rel in files:
                result[rel] = [files[rel], self.local_mark(rel)]
        self.save_manifest(result)
        return json.loads(out or b"{}").get("written", 0)

    async def reset(self) -> None:
        if self.user_dir.exists():
            proc = await asyncio.create_subprocess_exec(
                "/bin/rm", "-rf", "--", str(self.path), cwd="/", env={"PATH": config.BASE_PATH},
                **self._as_user())
            await proc.wait()
        self.manifest_file.unlink(missing_ok=True)

    # --- запуск ----------------------------------------------------------------

    def _as_user(self) -> dict:
        return {"user": self.uid, "group": self.uid, "extra_groups": []}

    def env(self) -> dict[str, str]:
        path = ":".join([str(self.path / ".venv" / "bin"), str(self.path / "node_modules" / ".bin"),
                         *config.EXTRA_PATH, config.BASE_PATH])
        env = {
            "PATH": path, "HOME": str(self.home), "TMPDIR": str(self.tmp), "USER": "layla",
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TERM": "dumb", "CI": "1",
            "NO_COLOR": "1", "FORCE_COLOR": "0", "PYTHONUNBUFFERED": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1", "PIP_PROGRESS_BAR": "off",
            "VIRTUAL_ENV": str(self.path / ".venv"),
            "npm_config_update_notifier": "false", "npm_config_fund": "false",
            "npm_config_audit": "false", "npm_config_progress": "false",
            "GOPATH": str(self.home / "go"), "GOFLAGS": "-modcacherw",
            "RUSTUP_HOME": "/opt/rustup", "CARGO_HOME": str(self.home / ".cargo"),
        }
        # Свой корневой сертификат (корпоративный прокси с подменой TLS) — тем же инструментам.
        for name in config.CA_ENV:
            if os.environ.get(name):
                env[name] = os.environ[name]
        if config.PROXY:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                env[name] = config.PROXY
            env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1"
        return env

    def _wrap(self, argv: list[str]) -> list[str]:
        # Команда первой уходит под OOM-killer (а не сервер песочницы) и получает пределы процесса.
        return ["/bin/sh", "-c", 'echo 1000 > /proc/self/oom_score_adj 2>/dev/null; exec "$@"', "sh",
                "prlimit", f"--nproc={config.NPROC}", f"--fsize={config.FSIZE}",
                f"--nofile={config.NOFILE}", "--core=0", "--", *argv]

    async def spawn(self, argv: list[str], stdin: bool) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *self._wrap(argv), cwd=str(self.path), env=self.env(),
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True, **self._as_user(),
        )

    async def kill_all(self) -> None:
        """Добить всё, что команда оставила (фоновые процессы, демоны): kill(-1) от uid."""
        script = "import os, signal\ntry:\n    os.kill(-1, signal.SIGKILL)\nexcept OSError:\n    pass\n"
        proc = await asyncio.create_subprocess_exec(PYTHON, "-I", "-c", script, cwd="/",
                                                    env={"PATH": config.BASE_PATH}, **self._as_user())
        await proc.wait()

    async def ensure_venv(self) -> bool:
        """Виртуальное окружение Python в рабочей копии: pip install работает сразу. True — создано."""
        if (self.path / ".venv" / "bin" / "python").exists():
            return False
        proc = await self.spawn(["python3", "-m", "venv", ".venv"], stdin=False)
        await proc.communicate()
        return proc.returncode == 0

    async def run(self, command: str, timeout: int, stdin: str | None = None) -> AsyncIterator[dict]:
        """Выполнить команду bash в рабочей копии: события output (по мере вывода) и exit."""
        started = time.monotonic()
        proc = await self.spawn(["/bin/bash", "-c", command], stdin=stdin is not None)
        feeder = asyncio.create_task(_feed(proc, stdin)) if stdin is not None else None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        size, truncated, timed_out = 0, False, False
        deadline = started + timeout
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    timed_out = True
                    break
                try:
                    chunk = await asyncio.wait_for(proc.stdout.read(8192), left)
                except TimeoutError:
                    timed_out = True
                    break
                if not chunk:
                    break
                if size < config.MAX_OUTPUT:
                    piece = chunk[: config.MAX_OUTPUT - size]
                    size += len(piece)
                    text = decoder.decode(piece)
                    if text:
                        yield {"type": "output", "data": text}
                    if len(piece) < len(chunk):
                        truncated = True
                else:
                    truncated = True  # дочитываем, чтобы процесс не встал на полном буфере
            if timed_out:
                _killpg(proc.pid)
            try:
                code = await asyncio.wait_for(proc.wait(), 10)
            except TimeoutError:
                _killpg(proc.pid)
                code = await proc.wait()
        finally:
            if proc.returncode is None:
                _killpg(proc.pid)
                with suppress(Exception):
                    await asyncio.wait_for(proc.wait(), 5)
            if feeder is not None:
                feeder.cancel()
            await self.kill_all()
        tail = decoder.decode(b"", final=True)
        if tail:
            yield {"type": "output", "data": tail}
        yield {
            "type": "exit",
            "code": code if code is not None and code >= 0 else None,
            "signal": -code if code is not None and code < 0 else None,
            "duration": round(time.monotonic() - started, 2),
            "timed_out": timed_out,
            "truncated": truncated,
        }


def _killpg(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


async def _feed(proc: asyncio.subprocess.Process, text: str) -> None:
    """stdin пишется отдельно от чтения вывода: иначе большой ввод и вывод заперли бы друг друга."""
    with suppress(Exception):
        proc.stdin.write(text.encode())
        await proc.stdin.drain()
    with suppress(Exception):
        proc.stdin.close()


def empty_tar() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w"):
        pass
    return buffer.getvalue()


def archive_names(archive: bytes) -> set[str]:
    if not archive:
        return set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        return {check_path(m.name) for m in tar.getmembers() if m.isfile()}
