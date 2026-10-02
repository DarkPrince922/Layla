"""Правка дизайна по клику: версия по брифу открывается в дизайн-чате копией своих файлов."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.design import Design
from app.models.enums import DesignStack
from app.models.user import Project, User

pytestmark = pytest.mark.asyncio


async def test_version_opens_in_a_design_chat_with_its_files(client, db_sessionmaker):
    await client.post("/api/auth/register", json={"email": "pick@example.com", "password": "hunter2hunter2"})
    async with db_sessionmaker() as session:
        user = await session.scalar(select(User).where(User.email == "pick@example.com"))
        design = Design(owner_id=user.id, stack=DesignStack.html, brief={"artifact_type": "Landing"},
                        files=[{"name": "index.html", "content": "<h1 class='hero'>Север</h1>", "language": "html"}])
        session.add(design)
        await session.commit()
        design_id = design.id
    response = await client.post(f"/api/designs/{design_id}/chat")
    assert response.status_code == 201, response.text
    chat = response.json()
    assert chat["domain"] == "design" and chat["title"] == "Правка версии · Landing"
    files = (await client.get(f"/api/projects/{chat['project_id']}/file?path=index.html")).json()
    assert files["content"] == "<h1 class='hero'>Север</h1>"
    async with db_sessionmaker() as session:
        project = await session.get(Project, chat["project_id"])
        assert project.kind == "chat_workspace" and Path(project.path).is_dir()
    # Сама версия не меняется, и чужую версию открыть нельзя.
    await client.post("/api/auth/logout")
    await client.post("/api/auth/register", json={"email": "other@example.com", "password": "hunter2hunter2"})
    assert (await client.post(f"/api/designs/{design_id}/chat")).status_code == 404
