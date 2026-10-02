"""Корзина на 7 дней и отделение рабочих папок чатов от проектов «Кода»."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from app.models.chat import Chat
from app.models.user import Project
from app.services import project_agent, trash
from tests.test_m3_migration import migrate

pytestmark = pytest.mark.asyncio


async def _setup(client, email="trash@example.com"):
    await client.post("/api/auth/register", json={"email": email, "password": "hunter2hunter2"})
    await client.post("/api/providers", json={"name": "M", "kind": "openai_compatible",
                                              "base_url": "http://p/v1", "default_model": "m", "active": True})


async def _chat_with_files(client, monkeypatch, domain="design"):
    """Чат, в котором агент записал файл: у него появилась своя рабочая папка."""
    async def fake_run(provider, key, model, payload, root, permissions, **kw):
        (Path(root) / "index.html").write_text("<h1>hi</h1>")
        yield {"delta": "ok"}

    monkeypatch.setattr(project_agent, "run", fake_run)
    chat = (await client.post("/api/chats", json={"domain": domain, "model": "m"})).json()
    job = (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "сделай"})).json()
    for _ in range(300):
        await asyncio.sleep(0.02)
        if (await client.get(f"/api/jobs/{job['id']}")).json()["status"] not in ("queued", "running"):
            break
    project_id = (await client.get(f"/api/chats/{chat['id']}")).json()["project_id"]
    return chat, project_id, job


async def _path(db_sessionmaker, project_id) -> Path:
    async with db_sessionmaker() as s:
        return Path((await s.get(Project, project_id)).path)


async def test_chat_workspace_is_not_a_code_project(client, monkeypatch, db_sessionmaker):
    await _setup(client)
    _, project_id, _ = await _chat_with_files(client, monkeypatch)
    assert all(p["id"] != project_id for p in (await client.get("/api/projects")).json())
    # Но файлы чата доступны (превью Дизайна, ZIP).
    files = (await client.get(f"/api/projects/{project_id}/files")).json()
    assert [f["name"] for f in files] == ["index.html"]


async def test_open_in_code_promotes_workspace(client, monkeypatch, db_sessionmaker):
    """«Открыть в Коде»: папка чата становится проектом и больше не уходит вместе с чатом."""
    await _setup(client)
    chat, project_id, _ = await _chat_with_files(client, monkeypatch)
    await client.patch(f"/api/chats/{chat['id']}", json={"title": "Лендинг студии"})
    promoted = (await client.post(f"/api/projects/{project_id}/promote")).json()
    assert promoted["kind"] == "project" and promoted["name"] == "Лендинг студии"
    assert any(p["id"] == project_id for p in (await client.get("/api/projects")).json())
    await client.delete(f"/api/chats/{chat['id']}")
    assert any(p["id"] == project_id for p in (await client.get("/api/projects")).json())


async def test_trash_and_restore_chat_with_its_files(client, monkeypatch, db_sessionmaker):
    await _setup(client)
    chat, project_id, job = await _chat_with_files(client, monkeypatch)
    folder = await _path(db_sessionmaker, project_id)
    assert (await client.delete(f"/api/chats/{chat['id']}")).status_code == 204
    listed = (await client.get("/api/trash")).json()
    assert [c["id"] for c in listed["chats"]] == [chat["id"]] and listed["days"] == 7
    assert (await client.get(f"/api/projects/{project_id}/files")).status_code == 404
    # Задачи удалённого чата не висят в «В работе».
    assert all(j["id"] != job["id"] for j in (await client.get("/api/jobs")).json())

    assert (await client.post(f"/api/trash/chats/{chat['id']}/restore")).status_code == 204
    assert (await client.get(f"/api/chats/{chat['id']}")).status_code == 200
    assert (folder / "index.html").exists()
    assert (await client.get("/api/trash")).json()["chats"] == []


async def test_chat_of_trashed_project_restores_only_with_project(client, db_sessionmaker):
    await _setup(client)
    project = (await client.post("/api/projects", json={"name": "Сайт"})).json()
    chat = (await client.post("/api/chats", json={"domain": "code", "project_id": project["id"]})).json()
    await client.delete(f"/api/projects/{project['id']}")
    assert (await client.post(f"/api/trash/chats/{chat['id']}/restore")).status_code == 409


async def test_clear_history_then_empty_trash(client, monkeypatch, db_sessionmaker):
    await _setup(client)
    chat, project_id, _ = await _chat_with_files(client, monkeypatch, domain="osint")
    folder = await _path(db_sessionmaker, project_id)
    assert (await client.delete("/api/chats", params={"domain": "osint"})).json() == {"deleted": 1, "skipped": 0}
    assert len((await client.get("/api/trash")).json()["chats"]) == 1
    assert folder.exists()
    assert (await client.delete("/api/trash")).status_code == 204
    assert (await client.get("/api/trash")).json() == {"days": 7, "chats": [], "projects": []}
    assert not folder.exists()


async def test_purge_removes_only_items_older_than_seven_days(client, monkeypatch, db_sessionmaker):
    await _setup(client)
    old_chat, old_project, _ = await _chat_with_files(client, monkeypatch)
    new_chat, _, _ = await _chat_with_files(client, monkeypatch)
    old_folder = await _path(db_sessionmaker, old_project)
    await client.delete(f"/api/chats/{old_chat['id']}")
    await client.delete(f"/api/chats/{new_chat['id']}")
    week_ago = trash.now() - timedelta(days=8)
    async with db_sessionmaker() as s:
        await s.execute(update(Chat).where(Chat.id == old_chat["id"]).values(deleted_at=week_ago))
        await s.execute(update(Project).where(Project.id == old_project).values(deleted_at=week_ago))
        await s.commit()
    assert await trash.purge(db_sessionmaker) >= 1  # старый чат вместе со своей папкой
    left = (await client.get("/api/trash")).json()["chats"]
    assert [c["id"] for c in left] == [new_chat["id"]]
    assert not old_folder.exists()


async def test_trash_is_private(client, monkeypatch, db_sessionmaker):
    await _setup(client)
    chat, _, _ = await _chat_with_files(client, monkeypatch)
    await client.delete(f"/api/chats/{chat['id']}")
    await client.post("/api/auth/logout")
    await _setup(client, "stranger@example.com")
    assert (await client.get("/api/trash")).json()["chats"] == []
    assert (await client.post(f"/api/trash/chats/{chat['id']}/restore")).status_code == 404
    assert (await client.delete(f"/api/trash/chats/{chat['id']}")).status_code == 404


def test_migration_marks_old_chat_folders(tmp_path):
    """Миграция 0012 узнаёт старые папки чатов и не трогает настоящие проекты."""
    path = tmp_path / "old.db"
    migrate(path, "0011_design_projects")
    with sqlite3.connect(path) as db:
        db.executescript("""
            INSERT INTO users (id, email, pw_hash, is_active, is_admin, must_change_password, created_at, updated_at)
              VALUES ('u', 'u@x', 'h', 1, 0, 0, '2026-01-01', '2026-01-01');
            INSERT INTO workspaces (id, owner_id, name, created_at, updated_at)
              VALUES ('w', 'u', 'W', '2026-01-01', '2026-01-01');
            INSERT INTO projects (id, workspace_id, name, created_at, updated_at) VALUES
              ('ws', 'w', 'DESIGN · лендинг', '2026-01-01', '2026-01-01'),
              ('code', 'w', 'Мой сайт', '2026-01-01', '2026-01-01'),
              ('shared', 'w', 'OSINT · общий', '2026-01-01', '2026-01-01');
            INSERT INTO chats (id, owner_id, domain, project_id, created_at, updated_at) VALUES
              ('c1', 'u', 'design', 'ws', '2026-01-01', '2026-01-01'),
              ('c2', 'u', 'code', 'code', '2026-01-01', '2026-01-01'),
              ('c3', 'u', 'osint', 'shared', '2026-01-01', '2026-01-01'),
              ('c4', 'u', 'code', 'shared', '2026-01-01', '2026-01-01');
        """)
    migrate(path)
    with sqlite3.connect(path) as db:
        kinds = dict(db.execute("SELECT id, kind FROM projects"))
    # Папку, которую уже открыли в «Коде» (есть code-чат), считаем проектом.
    assert kinds == {"ws": "chat_workspace", "code": "project", "shared": "project"}
