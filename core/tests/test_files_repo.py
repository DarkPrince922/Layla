"""Тесты безопасной работы с файлами и валидации импорта репо (спец. §5.7)."""
from __future__ import annotations

import pytest

from app.services import files, repo


def test_safe_join_blocks_traversal(tmp_path):
    (tmp_path / "inside.txt").write_text("ok")
    # Обычный путь — ок.
    assert files.safe_join(tmp_path, "inside.txt").name == "inside.txt"
    # Обход через ../ — заблокирован.
    with pytest.raises(ValueError):
        files.safe_join(tmp_path, "../secret")
    with pytest.raises(ValueError):
        files.safe_join(tmp_path, "../../etc/passwd")
    with pytest.raises(ValueError):
        files.safe_join(tmp_path, "/etc/passwd")


def test_list_dir_and_read(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("world")
    (tmp_path / ".git").mkdir()  # должен быть скрыт

    top = files.list_dir(tmp_path, ".")
    names = {e["name"] for e in top}
    assert "a.txt" in names and "sub" in names
    assert ".git" not in names

    assert files.read_text(tmp_path, "sub/b.txt") == "world"

    with pytest.raises(ValueError):
        files.read_text(tmp_path, "../outside")


def test_read_rejects_oversized(tmp_path, monkeypatch):
    big = tmp_path / "big.bin"
    big.write_text("x" * 100)
    monkeypatch.setattr(files, "MAX_FILE_BYTES", 10)
    with pytest.raises(ValueError):
        files.read_text(tmp_path, "big.bin")


def test_validate_repo_url():
    assert repo.validate_repo_url("https://github.com/a/b.git")
    assert repo.validate_repo_url("http://example.com/x")
    with pytest.raises(ValueError):
        repo.validate_repo_url("file:///etc/passwd")
    with pytest.raises(ValueError):
        repo.validate_repo_url("ssh://x")
    with pytest.raises(ValueError):
        repo.validate_repo_url("not a url")


def test_safe_dir_name():
    assert repo.safe_dir_name("https://github.com/acme/Repo.git") == "Repo"
    assert repo.safe_dir_name("https://x.com/a/weird name!!") == "weird-name"


@pytest.mark.asyncio
async def test_project_files_api_and_traversal_blocked(client, tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "projects_dir", str(tmp_path))
    await client.post(
        "/api/auth/register",
        json={"email": "code@example.com", "password": "hunter2hunter2"},
    )
    proj = (await client.post("/api/projects", json={"name": "demo"})).json()
    from pathlib import Path
    root = Path(proj["path"])
    (root / "readme.md").write_text("# hi")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print(1)")

    files_top = (await client.get(f"/api/projects/{proj['id']}/files?path=.")).json()
    names = {f["name"] for f in files_top}
    assert {"readme.md", "src"} <= names

    content = (
        await client.get(f"/api/projects/{proj['id']}/file?path=src/main.py")
    ).json()
    assert content["content"] == "print(1)"

    # Обход путей через API → 400.
    r = await client.get(f"/api/projects/{proj['id']}/file?path=../../etc/passwd")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_import_bad_url_rejected(client):
    await client.post(
        "/api/auth/register",
        json={"email": "imp@example.com", "password": "hunter2hunter2"},
    )
    r = await client.post("/api/projects/import", json={"repo_url": "file:///etc/passwd"})
    assert r.status_code == 400
