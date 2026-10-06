"""Тесты безопасной работы с файлами и валидации импорта репо (спец. §5.7)."""
from __future__ import annotations

import asyncio

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
async def test_clone_missing_git_raises_clear_error(tmp_path, monkeypatch):
    """Если git не установлен, clone() даёт понятный RuntimeError, а не 500."""

    async def _boom(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(repo.asyncio, "create_subprocess_exec", _boom)
    with pytest.raises(RuntimeError, match="git не установлен"):
        await repo.clone("https://github.com/a/b.git", tmp_path / "out")


@pytest.mark.asyncio
async def test_clone_disables_credential_prompt(tmp_path, monkeypatch):
    """Клон не должен зависать на приватных репо, ожидая ввод логина/пароля."""
    captured: dict = {}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def _fake_exec(*args, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return _Proc()

    monkeypatch.setattr(repo.asyncio, "create_subprocess_exec", _fake_exec)
    await repo.clone("https://github.com/a/b.git", tmp_path / "out")
    assert captured["env"].get("GIT_TERMINAL_PROMPT") == "0"


@pytest.mark.asyncio
async def test_import_bad_url_rejected(client):
    await client.post(
        "/api/auth/register",
        json={"email": "imp@example.com", "password": "hunter2hunter2"},
    )
    r = await client.post("/api/projects/import", json={"repo_url": "file:///etc/passwd"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_http_upload_binary_replace_conflict_and_archive(client):
    import hashlib
    import io
    import zipfile
    await client.post('/api/auth/register', json={'email': 'upload@example.com', 'password': 'hunter2hunter2'})
    project = (await client.post('/api/projects', json={'name': 'Upload'})).json()
    url = f"/api/projects/{project['id']}/upload?path=assets/icon.bin"
    assert (await client.get(url)).json()['sha256'] is None
    data = b'\x00\xff\x89image'
    result = await client.put(url, content=data, headers={'Content-Type': 'application/octet-stream'})
    assert result.status_code == 200
    sha = result.json()['after_sha256']
    assert sha == hashlib.sha256(data).hexdigest()
    assert (await client.get(url)).json()['sha256'] == sha
    assert (await client.put(url, content=b'overwrite')).status_code == 409
    edited = await client.put(url + '&expected_sha256=' + sha, content=b'\x00new')
    assert edited.status_code == 200 and edited.json()['operation'] == 'edit'
    assert (await client.put(url + '&expected_sha256=' + sha, content=b'stale')).status_code == 409
    archive = await client.get(f"/api/projects/{project['id']}/archive")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
        assert z.read('assets/icon.bin') == b'\x00new'


@pytest.mark.asyncio
async def test_upload_limits_paths_symlinks_and_owner(client, monkeypatch, tmp_path):
    from pathlib import Path
    await client.post('/api/auth/register', json={'email': 'up-owner@example.com', 'password': 'hunter2hunter2'})
    project = (await client.post('/api/projects', json={'name': 'Upload'})).json()
    prefix = f"/api/projects/{project['id']}/upload?path="
    for path in ('../outside', '/absolute', '.git/config'):
        assert (await client.put(prefix + path, content=b'data')).status_code == 400
    outside = tmp_path / 'outside.bin'
    outside.write_bytes(b'keep')
    (Path(project['path']) / 'link').symlink_to(outside)
    assert (await client.put(prefix + 'link', content=b'bad')).status_code == 400
    assert outside.read_bytes() == b'keep'
    monkeypatch.setattr(files, 'MAX_UPLOAD_BYTES', 3)
    assert (await client.put(prefix + 'large', content=b'1234')).status_code == 413
    assert not (Path(project['path']) / 'large').exists()
    await client.post('/api/auth/logout')
    assert (await client.put(prefix + 'no-auth', content=b'x')).status_code == 401
    await client.post('/api/auth/register', json={'email': 'up-other@example.com', 'password': 'hunter2hunter2'})
    assert (await client.put(prefix + 'foreign', content=b'x')).status_code == 404
    assert (await client.get(prefix + 'foreign')).status_code == 404
