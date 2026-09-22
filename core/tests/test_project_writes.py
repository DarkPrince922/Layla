"""Containment, concurrency guards, ownership and project ZIP integration."""

from __future__ import annotations

import io
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.config import get_settings
from app.services import files


@pytest.fixture
async def project(client, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "projects_dir", str(tmp_path / "projects"))
    await client.post(
        "/api/auth/register", json={"email": "files@example.com", "password": "hunter2hunter2"}
    )
    response = await client.post("/api/projects", json={"name": "Мой проект"})
    assert response.status_code == 201
    return response.json()


def test_create_edit_delete_and_conflicts(tmp_path):
    created = files.change_file(tmp_path, "src/app.py", "print(1)\n", None)
    assert created["operation"] == "create" and "+print(1)" in created["diff"]
    first = files.read_file(tmp_path, "src/app.py")
    with pytest.raises(files.FileConflict):
        files.change_file(tmp_path, "src/app.py", "oops", None)
    edited = files.change_file(tmp_path, "src/app.py", "print(2)\n", first["sha256"])
    assert "-print(1)" in edited["diff"] and "+print(2)" in edited["diff"]
    with pytest.raises(files.FileConflict):
        files.change_file(tmp_path, "src/app.py", None, first["sha256"])
    deleted = files.change_file(tmp_path, "src/app.py", None, edited["after_sha256"])
    assert deleted["operation"] == "delete" and not (tmp_path / "src/app.py").exists()


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "sub/../../escape",
        "/etc/passwd",
        "C:/escape",
        "a\\..\\escape",
        ".git/config",
        "a\0b",
        ".",
        "a/../x",
    ],
)
def test_write_blocks_invalid_paths(tmp_path, path):
    with pytest.raises((ValueError, FileNotFoundError)):
        files.change_file(tmp_path, path, "data", None)


def test_symlinks_hardlinks_and_special_files(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_text("private")
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "filelink").symlink_to(secret)
    os.link(secret, root / "hardlink")
    os.mkfifo(root / "pipe")
    for rel in ("link/secret", "filelink", "hardlink", "pipe"):
        with pytest.raises((ValueError, OSError)):
            files.read_file(root, rel)
        with pytest.raises((ValueError, OSError)):
            files.change_file(root, rel, "overwrite", None)
    assert files.list_dir(root) == []
    assert secret.read_text() == "private"
    with files.export_zip(root) as stream, zipfile.ZipFile(stream) as archive:
        assert archive.namelist() == []


def test_symlink_swap_after_path_validation_is_blocked(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original = files.safe_join

    def swap(base, rel):
        result = original(base, rel)
        (root / "sub").symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(files, "safe_join", swap)
    with pytest.raises(ValueError):
        files.change_file(root, "sub/escape", "never", None)
    assert not (outside / "escape").exists()


def test_simultaneous_edits_only_one_version_wins(tmp_path):
    first = files.change_file(tmp_path, "file", "original", None)

    def edit(text):
        try:
            files.change_file(tmp_path, "file", text, first["after_sha256"])
            return "ok"
        except files.FileConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, ["a", "b"])) == ["conflict", "ok"]


def test_text_limits_empty_file_and_newline_diff(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "MAX_FILE_BYTES", 10)
    for content in ("я" * 6, "binary\0"):
        with pytest.raises(ValueError):
            files.change_file(tmp_path, "file", content, None)
    assert not (tmp_path / "file").exists()
    first = files.change_file(tmp_path, "empty", "", None)
    assert files.read_file(tmp_path, "empty")["content"] == ""
    files.change_file(tmp_path, "empty", "a", first["after_sha256"])
    current = files.read_file(tmp_path, "empty")
    diff = files.change_file(tmp_path, "empty", "a\n", current["sha256"])["diff"]
    assert "No newline at end of file" in diff


async def test_local_project_crud_and_archive(client, project):
    pid = project["id"]
    assert project["repo_url"] is None and Path(project["path"]).is_dir()
    assert (await client.get(f"/api/projects/{pid}/files")).json() == []
    url = f"/api/projects/{pid}/file"
    created = await client.put(
        url, json={"path": "src/main.py", "content": "print(1)\n", "expected_sha256": None}
    )
    assert created.status_code == 200
    first = (await client.get(url, params={"path": "src/main.py"})).json()
    assert (
        await client.put(
            url, json={"path": "src/main.py", "content": "oops", "expected_sha256": None}
        )
    ).status_code == 409
    edited = (
        await client.put(
            url,
            json={
                "path": "src/main.py",
                "content": "print(2)\n",
                "expected_sha256": first["sha256"],
            },
        )
    ).json()
    root = Path(project["path"])
    (root / "image.bin").write_bytes(b"\x00\xff\x01")
    (root / "empty").mkdir()
    (root / ".git").mkdir()
    (root / ".git/config").write_text("excluded")
    (root / "node_modules").mkdir()
    (root / "node_modules/cache").write_text("excluded")
    zipped = await client.get(f"/api/projects/{pid}/archive")
    assert zipped.status_code == 200 and "filename*=" in zipped.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
        assert archive.read("src/main.py") == b"print(2)\n"
        assert archive.read("image.bin") == b"\x00\xff\x01"
        assert "empty/" in archive.namelist()
        assert not any(p.startswith((".git/", "node_modules/")) for p in archive.namelist())
    assert (
        await client.delete(url, params={"path": "src/main.py", "expected_sha256": first["sha256"]})
    ).status_code == 409
    deleted = await client.delete(
        url, params={"path": "src/main.py", "expected_sha256": edited["after_sha256"]}
    )
    assert deleted.status_code == 200 and deleted.json()["operation"] == "delete"
    assert (await client.get(url, params={"path": "src/main.py"})).status_code == 404


async def test_empty_zip_and_duplicate_names(client, project):
    second = (await client.post("/api/projects", json={"name": project["name"]})).json()
    assert second["path"] != project["path"]
    zipped = await client.get(f"/api/projects/{second['id']}/archive")
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
        assert not archive.namelist()
    assert (
        await client.post("/api/projects", json={"name": "bad", "path": "/etc"})
    ).status_code == 422
    assert (await client.post("/api/projects", json={"name": "   "})).status_code == 422


async def test_other_user_cannot_read_write_delete_zip(client, project):
    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/register", json={"email": "otherfiles@example.com", "password": "hunter2hunter2"}
    )
    base = f"/api/projects/{project['id']}"
    for suffix in ("/file?path=a", "/files", "/archive"):
        assert (await client.get(base + suffix)).status_code == 404
    assert (
        await client.put(
            base + "/file", json={"path": "x", "content": "x", "expected_sha256": None}
        )
    ).status_code == 404
    assert (
        await client.delete(base + "/file", params={"path": "x", "expected_sha256": "a" * 64})
    ).status_code == 404


def test_zip_limits(tmp_path, monkeypatch):
    (tmp_path / "file").write_bytes(b"12345")
    monkeypatch.setattr(files, "MAX_ZIP_BYTES", 4)
    with pytest.raises(ValueError):
        files.export_zip(tmp_path)
    monkeypatch.setattr(files, "MAX_ZIP_BYTES", 100)
    monkeypatch.setattr(files, "MAX_ZIP_ENTRIES", 0)
    with pytest.raises(ValueError):
        files.export_zip(tmp_path)
