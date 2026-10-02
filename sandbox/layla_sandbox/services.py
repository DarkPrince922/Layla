"""Долгоживущие процессы проекта — dev-сервер или статический сервер для превью приложения.

Процесс работает в своём сеансе от uid пользователя, в рабочей копии проекта. Порт
находится сам: песочница смотрит, какие порты начали слушать процессы этого сеанса
(предпочтительно $PORT, но подойдёт и порт по умолчанию, например 5173 у Vite).
Процесс останавливается по команде, при простое без запросов и по сроку жизни.
"""
from __future__ import annotations

import asyncio
import codecs
import json
import re
import secrets
import signal
import socket
import time
from contextlib import suppress
from pathlib import Path

from layla_sandbox import config
from layla_sandbox.workspace import PYTHON, Workspace, kill_session

PORTS = str(Path(__file__).with_name("ports.py"))
LOG_LIMIT = 200_000
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def default_command(ws: Workspace) -> str | None:
    """Команда запуска по файлам проекта: npm-скрипт dev/start, Django или статический сервер."""
    root = ws.path
    package = root / "package.json"
    if package.is_file() and not package.is_symlink():
        try:
            scripts = json.loads(package.read_text()[:500_000]).get("scripts") or {}
        except (OSError, ValueError, AttributeError):
            scripts = {}
        install = "" if (root / "node_modules").is_dir() else "npm install --no-audit --no-fund && "
        if "dev" in scripts:
            return install + "npm run dev"
        if "start" in scripts:
            return install + "npm start"
    if (root / "manage.py").is_file():
        return "python manage.py runserver 127.0.0.1:$PORT"
    if (root / "index.html").is_file():
        return "python3 -m http.server $PORT --bind 127.0.0.1"
    return None


class Service:
    def __init__(self, ws: Workspace, command: str) -> None:
        self.ws = ws
        self.command = command
        self.nonce = secrets.token_urlsafe(12)
        self.env_port = free_port()
        self.proc: asyncio.subprocess.Process | None = None
        self.port: int | None = None
        self.host = "127.0.0.1"
        self.state = "starting"  # starting | running | exited | stopped | no_port
        self.exit_code: int | None = None
        self.started = time.time()
        self.last_access = time.monotonic()
        self.logs = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._tasks: list[asyncio.Task] = []

    @property
    def key(self) -> tuple[int, str]:
        return self.ws.uid, self.ws.project

    def touch(self) -> None:
        self.last_access = time.monotonic()

    def info(self, logs: bool = False) -> dict:
        data = {"state": self.state, "command": self.command, "port": self.port, "nonce": self.nonce,
                "started": self.started, "exit_code": self.exit_code}
        if logs:
            data["logs"] = self.logs[-20_000:]
        return data

    def _log(self, text: str) -> None:
        self.logs = (self.logs + _ANSI.sub("", text))[-LOG_LIMIT:]

    async def start(self) -> None:
        env = {"PORT": str(self.env_port), "HOST": "127.0.0.1", "BROWSER": "none", "CI": "",
               "NODE_ENV": "development"}
        self.proc = await self.ws.spawn(["/bin/bash", "-c", self.command], stdin=False, extra_env=env)
        self._log(f"$ {self.command}\n")
        self._tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._find_port())]

    async def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        while chunk := await self.proc.stdout.read(8192):
            self._log(self._decoder.decode(chunk))
        code = await self.proc.wait()
        if self.state in ("starting", "running", "no_port"):
            self.state = "exited"
            self.exit_code = code
            self._log(f"\n[процесс завершился с кодом {code}]\n")

    async def _listening(self) -> list[dict]:
        assert self.proc is not None
        proc = await asyncio.create_subprocess_exec(
            PYTHON, "-I", PORTS, str(self.proc.pid), cwd="/", env={"PATH": config.BASE_PATH},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **self.ws._as_user())
        out, _ = await proc.communicate()
        try:
            return json.loads(out or b"[]")
        except ValueError:
            return []

    async def _find_port(self) -> None:
        deadline = time.monotonic() + config.SERVICE_START_WAIT
        while time.monotonic() < deadline and self.state == "starting":
            found = await self._listening()
            if found:
                chosen = next((f for f in found if f["port"] == self.env_port), None) or min(found, key=lambda f: f["port"])
                self.port = chosen["port"]
                hosts = {f["host"] for f in found if f["port"] == self.port}
                # Слушает только IPv6-loopback — ходим туда; иначе IPv4 (dual-stack «::» его принимает).
                self.host = "::1" if hosts == {"::1"} else "127.0.0.1"
                self.state = "running"
                self._log(f"\n[превью: порт {self.port}]\n")
                return
            await asyncio.sleep(0.5)
        if self.state == "starting":
            self.state = "no_port"
            self._log("\n[процесс так и не начал слушать порт — проверьте команду и логи]\n")

    async def stop(self, reason: str = "stopped") -> None:
        # Причину ставим до остановки: иначе чтение вывода увидит выход процесса и назовёт это «exited».
        if self.state != "exited":
            self.state = reason
        if self.proc is not None and self.proc.returncode is None:
            kill_session(self.proc.pid, signal.SIGTERM)
            with suppress(TimeoutError):
                await asyncio.wait_for(self.proc.wait(), 3)
            kill_session(self.proc.pid)
            with suppress(TimeoutError):
                await asyncio.wait_for(self.proc.wait(), 3)
        for task in self._tasks:
            task.cancel()
        self._log(f"\n[превью остановлено: {reason}]\n")

    @property
    def alive(self) -> bool:
        return self.state in ("starting", "running", "no_port")

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


class Registry:
    def __init__(self) -> None:
        self.items: dict[tuple[int, str], Service] = {}

    def get(self, uid: int, project: str) -> Service | None:
        return self.items.get((uid, project))

    def active_for(self, uid: int) -> bool:
        return any(s.alive for (u, _), s in self.items.items() if u == uid)

    async def start(self, ws: Workspace, command: str) -> Service:
        old = self.items.pop((ws.uid, ws.project), None)
        if old is not None:
            await old.stop("restarted")
        alive = [s for s in self.items.values() if s.alive]
        if sum(1 for s in alive if s.ws.uid == ws.uid) >= config.MAX_SERVICES_PER_USER:
            raise RuntimeError("Запущено слишком много превью — остановите одно из них.")
        if len(alive) >= config.MAX_SERVICES:
            raise RuntimeError("В песочнице уже работает максимум превью — попробуйте позже.")
        service = Service(ws, command)
        await service.start()
        self.items[service.key] = service
        return service

    async def stop(self, uid: int, project: str) -> bool:
        service = self.items.pop((uid, project), None)
        if service is None:
            return False
        await service.stop()
        return True

    async def reap(self) -> None:
        """Остановить простаивающие и слишком долгие превью, забыть завершившиеся."""
        now_mono, now = time.monotonic(), time.time()
        for key, service in list(self.items.items()):
            if service.alive and now_mono - service.last_access > config.SERVICE_IDLE:
                await service.stop("idle")
            elif service.alive and now - service.started > config.SERVICE_LIFETIME:
                await service.stop("lifetime")
            elif not service.alive and now_mono - service.last_access > config.SERVICE_IDLE:
                self.items.pop(key, None)
