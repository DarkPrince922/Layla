"""Клиент песочницы проекта (сервис sandbox/) и Piston.

Песочница — рабочая копия проекта, где команды выполняются с зависимостями: pip/npm
install, тесты, сборка. Проект остаётся источником истины: перед каждой командой копия
догоняет его (присылаются только изменённые файлы), а всё, что команда создаёт или меняет,
остаётся в копии и в проект не попадает.

Piston — отдельный изолированный запуск программ на десятках языков, без сети и зависимостей.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import stat
import tarfile
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.services import files

logger = logging.getLogger("layla.sandbox")

# Зависимости и сборки песочница делает сама — в копию их не везём.
SKIP_DIRS = files._SKIP_DIRS | {"venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", "target",
                                 ".gradle", ".turbo"}
MAX_FILE = 50 * 1024 * 1024  # больше — не копируем (медиа, дампы)
MAX_FILES = 20_000
MAX_TOTAL = 300 * 1024 * 1024
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 900
_CACHE_SECONDS = 20
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Za-z0-9]")


class SandboxError(RuntimeError):
    """Песочница недоступна или отказала — текст для пользователя и модели."""


# --- соединение ---------------------------------------------------------------

def _client(timeout: float | httpx.Timeout) -> httpx.AsyncClient:
    url = get_settings().sandbox_url.strip()
    if not url:
        raise SandboxError("Песочница не настроена (LAYLA_SANDBOX_URL).")
    if url.startswith("unix://"):
        path = url[len("unix://"):]
        if not os.path.exists(path):
            raise SandboxError("Песочница не запущена: нет сокета сервиса sandbox.")
        transport = httpx.AsyncHTTPTransport(uds=path)
        return httpx.AsyncClient(transport=transport, base_url="http://sandbox", timeout=timeout)
    return httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout)


_info_cache: tuple[float, dict | None] = (0.0, None)


async def info(*, fresh: bool = False) -> dict | None:
    """Инструменты песочницы (python, node, gcc…), сеть, пределы. None — недоступна."""
    global _info_cache
    at, value = _info_cache
    if not fresh and time.monotonic() - at < _CACHE_SECONDS:
        return value
    try:
        async with _client(httpx.Timeout(10, connect=2)) as client:
            response = await client.get("/info")
            response.raise_for_status()
            value = response.json()
    except (SandboxError, httpx.HTTPError, ValueError) as exc:
        logger.info("sandbox unavailable: %s", exc)
        value = None
    _info_cache = (time.monotonic(), value)
    return value


def _owner_key(owner_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", owner_id)[:64] or "user"


# --- рабочая копия -------------------------------------------------------------

def manifest(root: str) -> dict[str, str]:
    """Файлы проекта для копии: путь -> «размер:mtime». Без зависимостей, сборок и .git."""
    base = Path(root)
    found: dict[str, str] = {}
    total = 0
    for directory, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(directory, d)))
        for name in sorted(names):
            path = os.path.join(directory, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
                continue
            rel = os.path.relpath(path, base).replace(os.sep, "/")
            found[rel] = f"{st.st_size}:{st.st_mtime_ns}"
            total += st.st_size
            if len(found) > MAX_FILES or total > MAX_TOTAL:
                raise SandboxError("Проект слишком большой для песочницы (больше 20 000 файлов или 300 МБ).")
    return found


def pack(root: str, paths: list[str]) -> bytes:
    buffer = io.BytesIO()
    base = Path(root)
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for rel in paths:
            path = files.safe_join(base, rel)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            item = tarfile.TarInfo(rel)
            item.size = st.st_size
            item.mode = 0o755 if st.st_mode & 0o111 else 0o644
            item.mtime = int(st.st_mtime)
            with open(path, "rb") as handle:
                archive.addfile(item, handle)
    return buffer.getvalue()


async def _post_sync(client: httpx.AsyncClient, url: str, head: bytes, archive: bytes) -> dict:
    try:
        response = await client.post(url, content=head + archive)
    except httpx.HTTPError as exc:
        raise SandboxError(f"Песочница не отвечает: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise SandboxError(_detail(response, "Песочница не приняла файлы проекта"))
    return response.json()


async def sync(owner_id: str, project_id: str, root: str) -> None:
    """Догнать рабочую копию до текущего состояния проекта (присылаются только изменения)."""
    marks = await run_in_threadpool(manifest, root)
    head = json.dumps({"files": marks}).encode() + b"\n"
    url = f"/workspaces/{_owner_key(owner_id)}/{project_id}/sync"
    async with _client(httpx.Timeout(300, connect=5)) as client:
        for _ in range(3):
            result = await _post_sync(client, url, head, b"")
            if "need" not in result:
                return
            archive = await run_in_threadpool(pack, root, result["need"])
            result = await _post_sync(client, url, head, archive)
            if "need" not in result:
                return
            # Файлы менялись прямо во время отправки — новый манифест и ещё попытка.
            marks = await run_in_threadpool(manifest, root)
            head = json.dumps({"files": marks}).encode() + b"\n"
    raise SandboxError("Файлы проекта постоянно меняются — не удалось подготовить копию.")


async def run(owner_id: str, project_id: str, root: str, command: str, *,
              timeout: int = DEFAULT_TIMEOUT, stdin: str | None = None) -> AsyncIterator[dict]:
    """Выполнить команду в копии проекта. События: info / output / exit / error."""
    timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    yield {"type": "info", "data": "Синхронизирую файлы проекта…"}
    await sync(owner_id, project_id, root)
    body = {"command": command, "timeout": timeout, "stdin": stdin}
    url = f"/workspaces/{_owner_key(owner_id)}/{project_id}/run"
    # Своё время команды плюс запас на очередь и создание .venv.
    async with _client(httpx.Timeout(timeout + 600, connect=5)) as client:
        try:
            async with client.stream("POST", url, json=body) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise SandboxError(_detail(response, "Песочница отклонила команду"))
                async for line in response.aiter_lines():
                    if line.strip():
                        yield json.loads(line)
        except httpx.HTTPError as exc:
            raise SandboxError(f"Связь с песочницей прервалась: {type(exc).__name__}") from exc


async def reset(owner_id: str, project_id: str) -> None:
    async with _client(httpx.Timeout(120, connect=5)) as client:
        try:
            response = await client.delete(f"/workspaces/{_owner_key(owner_id)}/{project_id}")
        except httpx.HTTPError as exc:
            raise SandboxError(f"Песочница не отвечает: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise SandboxError(_detail(response, "Не удалось сбросить окружение"))


def _detail(response: httpx.Response, fallback: str) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return f"{fallback}: {detail}" if isinstance(detail, str) else fallback


def clean(text: str) -> str:
    """Вывод без цветовых кодов; у перерисованной строки (прогресс-бар через \\r) — её итог."""
    text = _ANSI.sub("", text)
    if "\r" not in text:
        return text
    lines = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        lines.append(line.rsplit("\r", 1)[-1])
    return "\n".join(lines)


def squeeze(text: str, limit: int) -> tuple[str, bool]:
    """Уместить вывод: начало и конец (ошибка обычно в конце). (текст, обрезан ли)."""
    if len(text) <= limit:
        return text, False
    head = limit // 5
    tail = limit - head
    return text[:head] + f"\n…[пропущено {len(text) - limit} символов]…\n" + text[-tail:], True


# --- Piston ----------------------------------------------------------------------

_runtimes_cache: tuple[float, list | None] = (0.0, None)


def _piston(timeout: float | httpx.Timeout) -> httpx.AsyncClient:
    url = get_settings().piston_url.strip()
    if not url:
        raise SandboxError("Piston не настроен (LAYLA_PISTON_URL).")
    return httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout)


async def runtimes(*, fresh: bool = False) -> list[dict] | None:
    """Установленные в Piston языки. None — Piston недоступен."""
    global _runtimes_cache
    at, value = _runtimes_cache
    if not fresh and time.monotonic() - at < _CACHE_SECONDS:
        return value
    try:
        async with _piston(httpx.Timeout(10, connect=2)) as client:
            response = await client.get("/api/v2/runtimes")
            response.raise_for_status()
            value = [r for r in response.json() if isinstance(r, dict) and r.get("language")]
    except (SandboxError, httpx.HTTPError, ValueError) as exc:
        logger.info("piston unavailable: %s", exc)
        value = None
    _runtimes_cache = (time.monotonic(), value)
    return value


def forget_runtimes() -> None:
    global _runtimes_cache
    _runtimes_cache = (0.0, None)


async def packages() -> list[dict]:
    async with _piston(httpx.Timeout(30, connect=3)) as client:
        try:
            response = await client.get("/api/v2/packages")
        except httpx.HTTPError as exc:
            raise SandboxError("Piston недоступен") from exc
        if response.status_code >= 400:
            raise SandboxError(_piston_error(response, "Piston не отдал список языков"))
        return response.json()


async def change_package(language: str, version: str, *, install: bool) -> dict:
    async with _piston(httpx.Timeout(1800, connect=5)) as client:
        try:
            response = await client.request("POST" if install else "DELETE", "/api/v2/packages",
                                            json={"language": language, "version": version})
        except httpx.HTTPError as exc:
            raise SandboxError("Piston недоступен") from exc
    forget_runtimes()
    if response.status_code >= 400:
        raise SandboxError(_piston_error(response, "Piston отклонил операцию"))
    return response.json()


def find_runtime(items: list[dict], language: str, version: str | None = None) -> dict | None:
    wanted = language.strip().lower()
    for item in items:
        names = {item["language"].lower(), *(a.lower() for a in item.get("aliases") or [])}
        if wanted in names and (not version or item.get("version", "").startswith(version)):
            return item
    return None


async def execute(language: str, version: str | None, sources: list[dict], *, stdin: str = "",
                  args: list[str] | None = None) -> dict:
    """Скомпилировать и запустить программу в Piston. sources: [{"name", "content"}], первый — точка входа."""
    items = await runtimes()
    if items is None:
        raise SandboxError("Piston недоступен: запуск программ на других языках сейчас невозможен.")
    runtime = find_runtime(items, language, version)
    if runtime is None:
        installed = ", ".join(sorted({r["language"] for r in items})) or "нет"
        raise SandboxError(f"Язык «{language}» не установлен в Piston. Установлены: {installed}.")
    body = {"language": runtime["language"], "version": runtime["version"], "files": sources,
            "stdin": stdin or "", "args": args or []}
    async with _piston(httpx.Timeout(300, connect=5)) as client:
        try:
            response = await client.post("/api/v2/execute", json=body)
        except httpx.HTTPError as exc:
            raise SandboxError(f"Связь с Piston прервалась: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise SandboxError(_piston_error(response, "Piston не выполнил программу"))
    return response.json()


def _piston_error(response: httpx.Response, fallback: str) -> str:
    try:
        message = response.json().get("message")
    except ValueError:
        message = None
    return f"{fallback}: {message}" if isinstance(message, str) else fallback


# --- превью приложения: долгоживущий процесс в песочнице ------------------------

_previews: dict[tuple[str, str], str] = {}  # (владелец, проект) -> корень проекта, пока превью живо
_pending_sync: dict[tuple[str, str], asyncio.Task] = {}


def _service_url(owner_id: str, project_id: str, tail: str = "") -> str:
    return f"/workspaces/{_owner_key(owner_id)}/{project_id}/service{tail}"


async def _service_call(method: str, url: str, *, json_body: dict | None = None, params: dict | None = None,
                        timeout: float = 60) -> dict:
    async with _client(httpx.Timeout(timeout, connect=5)) as client:
        try:
            response = await client.request(method, url, json=json_body, params=params)
        except httpx.HTTPError as exc:
            raise SandboxError(f"Песочница не отвечает: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise SandboxError(_detail(response, "Песочница отказала"))
    return response.json()


async def preview_start(owner_id: str, project_id: str, root: str, command: str | None = None) -> dict:
    """Синхронизировать файлы и запустить превью (dev-сервер или статический сервер)."""
    await sync(owner_id, project_id, root)
    data = await _service_call("POST", _service_url(owner_id, project_id), json_body={"command": command})
    _previews[(owner_id, project_id)] = root
    return data


async def preview_status(owner_id: str, project_id: str, *, logs: bool = False, root: str | None = None) -> dict:
    """Состояние превью. С root живое превью снова отслеживается для синхронизации правок —
    например, после перезапуска ядра, пока превью в песочнице продолжало работать."""
    data = await _service_call("GET", _service_url(owner_id, project_id), params={"logs": logs} if logs else None,
                               timeout=15)
    if data.get("state") not in ("starting", "running", "no_port"):
        _previews.pop((owner_id, project_id), None)
    elif root is not None:
        _previews[(owner_id, project_id)] = root
    return data


async def preview_stop(owner_id: str, project_id: str) -> dict:
    _previews.pop((owner_id, project_id), None)
    return await _service_call("DELETE", _service_url(owner_id, project_id), timeout=30)


async def preview_fetch(owner_id: str, project_id: str, path: str = "/") -> dict:
    return await _service_call("GET", _service_url(owner_id, project_id, "/fetch"), params={"path": path},
                               timeout=60)


async def preview_wait(owner_id: str, project_id: str, timeout: float = 150) -> dict:
    """Дождаться, пока превью начнёт отвечать (или упадёт)."""
    deadline = time.monotonic() + timeout
    status: dict = {}
    while time.monotonic() < deadline:
        status = await preview_status(owner_id, project_id, logs=True)
        if status.get("state") != "starting":
            return status
        await asyncio.sleep(1)
    return status


def preview_touch(owner_id: str, project_id: str) -> None:
    """Файлы проекта изменились — если превью запущено, донести их в песочницу (с задержкой)."""
    key = (owner_id, project_id)
    root = _previews.get(key)
    if root is None:
        return
    pending = _pending_sync.get(key)
    if pending is not None and not pending.done():
        pending.cancel()

    async def later() -> None:
        await asyncio.sleep(0.8)  # пачка правок подряд — одна синхронизация
        try:
            await sync(owner_id, project_id, root)
        except SandboxError as exc:
            logger.info("preview sync failed: %s", exc)

    _pending_sync[key] = asyncio.get_running_loop().create_task(later())


def sandbox_socket() -> str | None:
    """Путь unix-сокета песочницы (для WebSocket-прокси) или None, если песочница по http."""
    url = get_settings().sandbox_url.strip()
    return url[len("unix://"):] if url.startswith("unix://") else None


def sandbox_http_base() -> str:
    url = get_settings().sandbox_url.strip()
    return "http://sandbox" if url.startswith("unix://") else url.rstrip("/")


def preview_path(owner_id: str, project_id: str, kind: str, path: str) -> str:
    return _service_url(owner_id, project_id, f"/{kind}/{path.lstrip('/')}")
