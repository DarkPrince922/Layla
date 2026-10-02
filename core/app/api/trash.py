"""Корзина: список удалённого, восстановление, окончательное удаление (7 дней)."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.chat import Chat
from app.models.enums import Domain
from app.models.user import Project, User, Workspace
from app.services import audit, trash
from app.services.auth import get_current_user

router = APIRouter(prefix="/trash", tags=["trash"])


class TrashChat(BaseModel):
    id: str
    title: str | None = None
    domain: Domain
    deleted_at: datetime
    purge_at: datetime


class TrashProject(BaseModel):
    id: str
    name: str
    chats: int
    deleted_at: datetime
    purge_at: datetime


class TrashOut(BaseModel):
    days: int = trash.TRASH_DAYS
    chats: list[TrashChat]
    projects: list[TrashProject]


def _purge_at(when: datetime) -> datetime:
    return when + timedelta(days=trash.TRASH_DAYS)


def _user_projects(user: User):
    return select(Project).join(Workspace, Project.workspace_id == Workspace.id).where(Workspace.owner_id == user.id)


async def _trashed_chat(session: AsyncSession, user: User, chat_id: str) -> Chat:
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.owner_id != user.id or chat.deleted_at is None:
        raise HTTPException(status_code=404, detail="В корзине такого чата нет")
    return chat


async def _trashed_project(session: AsyncSession, user: User, project_id: str) -> Project:
    project = await session.scalar(_user_projects(user).where(Project.id == project_id))
    if project is None or project.deleted_at is None:
        raise HTTPException(status_code=404, detail="В корзине такого проекта нет")
    return project


@router.get("", response_model=TrashOut)
async def list_trash(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TrashOut:
    # Чаты, ушедшие в корзину вместе с проектом «Кода», показываем внутри проекта.
    chats = await session.scalars(
        select(Chat)
        .outerjoin(Project, Chat.project_id == Project.id)
        .where(Chat.owner_id == user.id, Chat.deleted_at.is_not(None))
        .where(~and_(Project.deleted_at.is_not(None), Project.kind == "project"))
        .order_by(Chat.deleted_at.desc())
    )
    projects = list(await session.scalars(
        _user_projects(user)
        .where(Project.deleted_at.is_not(None), Project.kind == "project")
        .order_by(Project.deleted_at.desc())
    ))
    counts = {}
    for project in projects:
        counts[project.id] = await session.scalar(
            select(func.count()).select_from(Chat)
            .where(Chat.project_id == project.id, Chat.deleted_at == project.deleted_at)
        )
    return TrashOut(
        chats=[TrashChat(id=c.id, title=c.title, domain=c.domain, deleted_at=c.deleted_at,
                         purge_at=_purge_at(c.deleted_at)) for c in chats],
        projects=[TrashProject(id=p.id, name=p.name, chats=counts[p.id] or 0, deleted_at=p.deleted_at,
                               purge_at=_purge_at(p.deleted_at)) for p in projects],
    )


@router.post("/chats/{chat_id}/restore", status_code=204)
async def restore_chat(chat_id: str, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> None:
    chat = await _trashed_chat(session, user, chat_id)
    try:
        await trash.restore_chat(session, chat)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit.record(session, actor=user.id, action="chat.restore", target=chat_id)
    await session.commit()


@router.post("/projects/{project_id}/restore", status_code=204)
async def restore_project(project_id: str, user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)) -> None:
    project = await _trashed_project(session, user, project_id)
    await trash.restore_project(session, project)
    await audit.record(session, actor=user.id, action="project.restore", target=project_id)
    await session.commit()


@router.delete("/chats/{chat_id}", status_code=204)
async def purge_chat(chat_id: str, user: User = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)) -> None:
    chat = await _trashed_chat(session, user, chat_id)
    folder = await trash.remove_chat(session, chat)
    await audit.record(session, actor=user.id, action="chat.purge", target=chat_id)
    await session.commit()
    await trash.purge_dirs([folder] if folder else [])


@router.delete("/projects/{project_id}", status_code=204)
async def purge_project(project_id: str, user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)) -> None:
    project = await _trashed_project(session, user, project_id)
    folder = await trash.remove_project(session, project)
    await audit.record(session, actor=user.id, action="project.purge", target=project_id)
    await session.commit()
    await trash.purge_dirs([folder] if folder else [])


@router.delete("", status_code=204)
async def empty_trash(user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)) -> None:
    folders: list[Path] = []
    for project in list(await session.scalars(_user_projects(user).where(Project.deleted_at.is_not(None)))):
        folder = await trash.remove_project(session, project)
        if folder:
            folders.append(folder)
    await session.flush()
    for chat in list(await session.scalars(
        select(Chat).where(Chat.owner_id == user.id, Chat.deleted_at.is_not(None))
    )):
        folder = await trash.remove_chat(session, chat)
        if folder:
            folders.append(folder)
    await audit.record(session, actor=user.id, action="trash.empty", target=user.id)
    await session.commit()
    await trash.purge_dirs(folders)
