"""Безопасная работа с файлами проекта (спец. §5.7).

Все операции строго ограничены каталогом проекта. Любая попытка выйти за его
пределы (обход путей через ../, симлинки и т.п.) блокируется.
"""
from __future__ import annotations

from pathlib import Path

MAX_FILE_BYTES = 1_000_000  # читаемый текстовый файл до ~1 МБ
_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".next"}


def safe_join(base: str | Path, rel: str) -> Path:
    """Присоединить относительный путь к base, гарантируя, что результат внутри base.

    Бросает ValueError при выходе за пределы base.
    """
    base_path = Path(base).resolve()
    # Нормализуем и запрещаем абсолютные пути в rel.
    rel = (rel or ".").lstrip("/")
    target = (base_path / rel).resolve()
    if base_path != target and base_path not in target.parents:
        raise ValueError("Путь вне каталога проекта")
    return target


def list_dir(base: str | Path, rel: str = ".") -> list[dict]:
    """Листинг одного уровня каталога (для ленивого дерева файлов)."""
    target = safe_join(base, rel)
    if not target.exists() or not target.is_dir():
        raise FileNotFoundError(rel)
    entries: list[dict] = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.name in _SKIP_DIRS:
            continue
        entries.append(
            {
                "name": child.name,
                "path": str(child.relative_to(Path(base).resolve())),
                "is_dir": child.is_dir(),
            }
        )
    return entries


def read_text(base: str | Path, rel: str) -> str:
    """Прочитать текстовый файл в пределах проекта (с лимитом размера)."""
    target = safe_join(base, rel)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(rel)
    if target.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Файл слишком большой для просмотра")
    return target.read_text(encoding="utf-8", errors="replace")
