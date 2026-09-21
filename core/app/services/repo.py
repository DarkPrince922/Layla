"""Импорт git-репозиториев (спец. §5.7).

Клонирование выполняется на сервере оператора (single-tenant). URL валидируется
(только http/https/git, есть хост), имя каталога санитизируется, клон делается
поверхностным (--depth 1) в каталог проектов.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https", "git"}
_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def validate_repo_url(url: str) -> str:
    """Проверить URL репозитория; вернуть нормализованный или бросить ValueError."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError("Недопустимая схема URL (разрешены http/https/git)")
    if not parsed.netloc:
        raise ValueError("В URL отсутствует хост")
    return url


def safe_dir_name(url: str) -> str:
    """Извлечь безопасное имя каталога из URL репозитория."""
    tail = urlparse(url).path.rstrip("/").split("/")[-1] or "repo"
    tail = tail[:-4] if tail.endswith(".git") else tail
    name = _NAME_RE.sub("-", tail).strip("-.")
    return name or "repo"


async def clone(url: str, dest: str | Path, *, timeout: float = 300.0) -> None:
    """Поверхностно клонировать репозиторий в dest. Бросает RuntimeError при ошибке."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", url, str(dest),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise RuntimeError("Таймаут клонирования репозитория") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"git clone завершился с ошибкой: {stderr.decode(errors='replace')[:500]}")
