"""HTTP API песочницы на unix-сокете (только для ядра Layla).

POST /workspaces/{owner}/{project}/sync   тело: строка JSON {"files": {путь: отпечаток}} + tar
    → {"need": [...]}: каких файлов не хватает (ничего не применено), или {"ok": true, ...}
POST /workspaces/{owner}/{project}/run    {"command", "timeout", "stdin"} → NDJSON-поток событий
DELETE /workspaces/{owner}/{project}      сбросить рабочую копию (зависимости, сборки)
GET /info                                 языки и инструменты, сеть, пределы

Превью приложения (долгоживущий процесс проекта):
POST|GET|DELETE /workspaces/{owner}/{project}/service      запуск / состояние / остановка
*    /workspaces/{owner}/{project}/service/http/{path}     HTTP к приложению
WS   /workspaces/{owner}/{project}/service/ws/{path}       WebSocket к приложению (HMR)
GET  /workspaces/{owner}/{project}/service/fetch?path=     страница глазами агента
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from collections import defaultdict
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from layla_sandbox import config
from layla_sandbox.services import Registry, default_command
from layla_sandbox.workspace import Users, Workspace, archive_names, check_path, prepare_root

log = logging.getLogger("layla.sandbox")
users = Users(config.WORK)
services = Registry()


async def _reaper() -> None:
    """Раз в полминуты останавливать простаивающие и слишком долгие превью."""
    while True:
        await asyncio.sleep(30)
        try:
            await services.reap()
        except Exception:  # уборка не должна падать насовсем из-за одного превью
            log.exception("reap failed")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    task = asyncio.create_task(_reaper())
    try:
        yield
    finally:
        task.cancel()
        for key in list(services.items):
            await services.stop(*key)


app = FastAPI(title="Layla sandbox", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)
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
                # Работающее превью пользователя не трогаем: добиваем только процессы команды.
                cleanup_all = not services.active_for(ws.uid)
                async for event in ws.run(body.command, timeout, body.stdin, cleanup_all=cleanup_all):
                    yield _line(event)
            except Exception as exc:  # причина уходит клиенту событием, поток не рвётся
                log.exception("run failed")
                yield _line({"type": "error", "data": f"Не удалось выполнить команду: {exc}"})

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.delete("/workspaces/{owner}/{project}")
async def reset(owner: str, project: str) -> dict:
    ws = _workspace(owner, project)
    await services.stop(ws.uid, ws.project)
    async with _locks[ws.uid]:
        await ws.reset()
    return {"ok": True}


# --- превью: долгоживущий процесс и доступ к нему ------------------------------

class ServiceIn(BaseModel):
    command: str | None = Field(default=None, max_length=4000)


def _service(owner: str, project: str):
    ws = _workspace(owner, project)
    service = services.get(ws.uid, ws.project)
    if service is None:
        raise HTTPException(status_code=404, detail="Превью не запущено")
    return service


@app.post("/workspaces/{owner}/{project}/service")
async def start_service(owner: str, project: str, body: ServiceIn) -> dict:
    ws = _workspace(owner, project)
    if not ws.path.is_dir():
        raise HTTPException(status_code=409, detail="Сначала синхронизируйте файлы проекта")
    command = (body.command or "").strip() or default_command(ws)
    if not command:
        raise HTTPException(status_code=400, detail="Не знаю, как запустить этот проект — укажите команду "
                                                    "(например, npm run dev или python app.py).")
    if not (ws.path / ".venv").exists():
        await ws.ensure_venv()
    try:
        service = await services.start(ws, command)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return service.info(logs=True)


@app.get("/workspaces/{owner}/{project}/service")
async def service_status(owner: str, project: str, logs: bool = False) -> dict:
    ws = _workspace(owner, project)
    service = services.get(ws.uid, ws.project)
    if service is None:
        return {"state": "none", "suggested": default_command(ws) if ws.path.is_dir() else None}
    return service.info(logs=logs)


@app.delete("/workspaces/{owner}/{project}/service")
async def stop_service(owner: str, project: str) -> dict:
    ws = _workspace(owner, project)
    return {"stopped": await services.stop(ws.uid, ws.project)}


_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
        "transfer-encoding", "upgrade", "host", "content-length", "accept-encoding"}
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5), follow_redirects=False)
    return _client


def _running(owner: str, project: str):
    service = _service(owner, project)
    if service.state != "running":
        raise HTTPException(status_code=503, detail=f"Превью не готово: {service.state}")
    service.touch()
    return service


def _local_headers(service, headers) -> dict[str, str]:
    # Dev-серверы пускают только «свой» хост и источник — представляемся локальным браузером.
    out = {k: v for k, v in headers.items() if k.lower() not in _HOP}
    out["host"] = f"localhost:{service.port}"
    out["accept-encoding"] = "identity"
    if "origin" in {k.lower() for k in out}:
        out = {k: v for k, v in out.items() if k.lower() != "origin"}
        out["origin"] = f"http://localhost:{service.port}"
    return out


@app.api_route("/workspaces/{owner}/{project}/service/http/{path:path}",
               methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def service_http(owner: str, project: str, path: str, request: Request) -> Response:
    service = _running(owner, project)
    url = f"{service.base_url}/{path}" + (f"?{request.url.query}" if request.url.query else "")
    headers = _local_headers(service, request.headers)
    upstream = _http().build_request(request.method, url, headers=headers, content=_body(request, headers))
    try:
        response = await _http().send(upstream, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Приложение не отвечает: {type(exc).__name__}") from exc
    headers = []
    local = (f"http://localhost:{service.port}", f"http://127.0.0.1:{service.port}")
    for key, value in response.headers.multi_items():
        name = key.lower()
        if name in _HOP:
            continue
        if name == "location":
            for prefix in local:
                if value.startswith(prefix):
                    value = value[len(prefix):] or "/"
        if name == "set-cookie":
            value = re.sub(r";\s*domain=[^;]*", "", value, flags=re.IGNORECASE)
        headers.append((key, value))
    streamed = StreamingResponse(response.aiter_raw(), status_code=response.status_code,
                                 background=BackgroundTask(response.aclose))
    streamed.raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers]
    return streamed


def _body(request: Request, headers: dict[str, str]):
    """Тело запроса — только если оно есть. Поток без длины httpx шлёт как chunked, а строгие
    серверы (и часть dev-серверов) GET с Transfer-Encoding отвергают."""
    length = request.headers.get("content-length")
    if length is not None:
        if length.strip() in ("", "0"):
            return None
        headers["content-length"] = length.strip()
        return request.stream()
    return request.stream() if "transfer-encoding" in request.headers else None


@app.websocket("/workspaces/{owner}/{project}/service/ws/{path:path}")
async def service_ws(websocket: WebSocket, owner: str, project: str, path: str) -> None:
    try:
        service = _running(owner, project)
    except HTTPException:
        await websocket.close(code=1013)
        return
    query = websocket.url.query
    uri = f"ws://localhost:{service.port}/{path}" + (f"?{query}" if query else "")
    protocols = [p.strip() for p in websocket.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    try:
        upstream = await ws_connect(uri, host=service.host, port=service.port, subprotocols=protocols or None,
                                    origin=f"http://localhost:{service.port}", max_size=None,
                                    open_timeout=10, ping_interval=None)
    except (OSError, TimeoutError, WebSocketException):  # dev-сервер не принял соединение
        await websocket.close(code=1011)
        return
    await websocket.accept(subprotocol=upstream.subprotocol)

    async def down() -> None:
        async for message in upstream:
            service.touch()
            if isinstance(message, bytes):
                await websocket.send_bytes(message)
            else:
                await websocket.send_text(message)

    async def up() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            service.touch()
            if message.get("bytes") is not None:
                await upstream.send(message["bytes"])
            elif message.get("text") is not None:
                await upstream.send(message["text"])

    tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        await upstream.close()
        with suppress(Exception):
            await websocket.close()


_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_DROP = re.compile(r"<(script|style|noscript|template)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


@app.get("/workspaces/{owner}/{project}/service/fetch")
async def service_fetch(owner: str, project: str, path: str = "/") -> dict:
    """Открыть страницу превью сервером — для проверки агентом (без выполнения JS)."""
    service = _service(owner, project)
    result: dict = {"state": service.state, "logs_tail": "\n".join(service.logs.splitlines()[-40:])}
    if service.state != "running":
        return result
    service.touch()
    if not path.startswith("/"):
        path = "/" + path
    try:
        response = await _http().get(f"{service.base_url}{path}", headers={"host": f"localhost:{service.port}"},
                                     follow_redirects=True, timeout=30)
    except httpx.HTTPError as exc:
        return {**result, "error": f"Приложение не отвечает: {type(exc).__name__}"}
    kind = response.headers.get("content-type", "")
    body = response.text[:500_000] if "text" in kind or "json" in kind or "javascript" in kind else ""
    title = _TITLE.search(body)
    text = re.sub(r"\s+", " ", _TAG.sub(" ", _DROP.sub(" ", body))).strip() if "html" in kind else body
    await asyncio.sleep(0.3)  # ошибки сборки часто появляются в логах сразу после запроса
    return {**result, "status": response.status_code, "content_type": kind,
            "title": (title.group(1).strip() if title else None), "text": text[:3000], "bytes": len(response.content),
            "logs_tail": "\n".join(service.logs.splitlines()[-40:])}


def main() -> None:
    import uvicorn

    prepare_root(config.WORK)
    socket = Path(config.SOCKET)
    socket.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(socket.parent, 0o700)  # к сокету доступ только у root — не у кода в песочнице
    socket.unlink(missing_ok=True)
    info()
    # Обычный цикл asyncio: команды запускаются от uid пользователя (user=/group= у subprocess),
    # а uvloop этих аргументов не поддерживает. WebSocket превью обслуживает библиотека websockets.
    try:
        from uvicorn.protocols.websockets import websockets_sansio_impl  # noqa: F401
        ws = "websockets-sansio"
    except ImportError:  # uvicorn до 0.35
        ws = "websockets"
    uvicorn.run(app, uds=str(socket), loop="asyncio", ws=ws, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
