"""HTTP API песочницы на unix-сокете (только для ядра Layla).

POST /workspaces/{owner}/{project}/sync   тело: строка JSON {"files": {путь: отпечаток}} + tar
    → {"need": [...]}: каких файлов не хватает (ничего не применено), или {"ok": true, ...}
POST /workspaces/{owner}/{project}/run    {"command", "timeout", "stdin"} → NDJSON-поток событий
DELETE /workspaces/{owner}/{project}      сбросить рабочую копию (зависимости, сборки)
GET /info                                 языки и инструменты, сеть, пределы
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from layla_sandbox import config
from layla_sandbox.workspace import Users, Workspace, archive_names, check_path, prepare_root

log = logging.getLogger("layla.sandbox")
app = FastAPI(title="Layla sandbox", docs_url=None, redoc_url=None, openapi_url=None)
users = Users(config.WORK)
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_slots: asyncio.Semaphore | None = None
_info: dict | None = None

# Что показать в настройках: инструмент -> команда версии.
_PROBES = {
    "python": ["python3", "--version"],
    "pip": ["pip3", "--version"],
    "node": ["node", "--version"],
    "npm": ["npm", "--version"],
    "gcc": ["gcc", "-dumpfullversion"],
    "g++": ["g++", "-dumpfullversion"],
    "make": ["make", "--version"],
    "git": ["git", "--version"],
    "go": ["go", "version"],
    "rustc": ["rustc", "--version"],
    "cargo": ["cargo", "--version"],
    "java": ["java", "-version"],
    "mvn": ["mvn", "--version"],
}


def _probe() -> dict[str, str]:
    env = {"PATH": ":".join([*config.EXTRA_PATH, config.BASE_PATH]), "HOME": "/tmp",
           "RUSTUP_HOME": "/opt/rustup", "CARGO_HOME": "/opt/cargo"}
    found = {}
    for name, argv in _PROBES.items():
        if not shutil.which(argv[0], path=env["PATH"]):
            continue
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=20, env=env, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        line = (done.stdout.strip() or done.stderr.strip()).splitlines()
        if line:
            found[name] = line[0][:120]
    return found


def info() -> dict:
    global _info
    if _info is None:
        _info = {
            "tools": _probe(),
            "network": "proxy" if config.PROXY else "off",
            "limits": {"max_timeout": config.MAX_TIMEOUT, "default_timeout": config.DEFAULT_TIMEOUT,
                       "max_output": config.MAX_OUTPUT, "max_parallel": config.MAX_PARALLEL},
        }
    return _info


def _slots_sem() -> asyncio.Semaphore:
    global _slots
    if _slots is None:
        _slots = asyncio.Semaphore(max(1, config.MAX_PARALLEL))
    return _slots


def _workspace(owner: str, project: str) -> Workspace:
    try:
        return Workspace(config.WORK, users.uid(owner), owner, project)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/info")
async def get_info() -> dict:
    return await asyncio.to_thread(info)


@app.post("/workspaces/{owner}/{project}/sync")
async def sync(owner: str, project: str, request: Request) -> dict:
    ws = _workspace(owner, project)
    size = int(request.headers.get("content-length") or 0)
    if size > config.MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="Слишком большой объём файлов для песочницы")
    body = await request.body()
    head, _, archive = body.partition(b"\n")
    try:
        files = json.loads(head or b"{}").get("files") or {}
        if not isinstance(files, dict) or len(files) > config.MAX_FILES:
            raise ValueError("Слишком много файлов в проекте для песочницы")
        files = {check_path(rel): str(mark) for rel, mark in files.items()}
        names = archive_names(archive)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # битый tar
        raise HTTPException(status_code=400, detail="Не удалось прочитать архив файлов") from exc
    async with _locks[ws.uid]:
        need, remove = await asyncio.to_thread(ws.plan, files, names)
        if need:
            return {"need": need}
        written = await ws.apply(files, archive, names & set(files), remove)
    return {"ok": True, "written": written, "removed": len(remove)}


class RunIn(BaseModel):
    command: str = Field(min_length=1, max_length=20_000)
    timeout: int = Field(default=config.DEFAULT_TIMEOUT, ge=1)
    stdin: str | None = Field(default=None, max_length=1_000_000)


def _line(event: dict) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode()


@app.post("/workspaces/{owner}/{project}/run")
async def run(owner: str, project: str, body: RunIn) -> StreamingResponse:
    ws = _workspace(owner, project)
    timeout = min(body.timeout, config.MAX_TIMEOUT)

    async def stream():
        lock, slots = _locks[ws.uid], _slots_sem()
        if lock.locked() or slots.locked():
            yield _line({"type": "info", "data": "Ждёт очереди: выполняется другая команда"})
        async with slots, lock:
            try:
                if not ws.path.is_dir():
                    await ws.apply({}, b"", set(), [])
                if not (ws.path / ".venv").exists():
                    yield _line({"type": "info", "data": "Создаю окружение Python (.venv)…"})
                    await ws.ensure_venv()
                async for event in ws.run(body.command, timeout, body.stdin):
                    yield _line(event)
            except Exception as exc:  # причина уходит клиенту событием, поток не рвётся
                log.exception("run failed")
                yield _line({"type": "error", "data": f"Не удалось выполнить команду: {exc}"})

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.delete("/workspaces/{owner}/{project}")
async def reset(owner: str, project: str) -> dict:
    ws = _workspace(owner, project)
    async with _locks[ws.uid]:
        await ws.reset()
    return {"ok": True}


def main() -> None:
    import uvicorn

    prepare_root(config.WORK)
    socket = Path(config.SOCKET)
    socket.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(socket.parent, 0o700)  # к сокету доступ только у root — не у кода в песочнице
    socket.unlink(missing_ok=True)
    info()
    uvicorn.run(app, uds=str(socket), log_level="info", access_log=False)


if __name__ == "__main__":
    main()
