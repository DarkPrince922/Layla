"""Корзина: удалённые чаты и проекты хранятся 7 дней, потом стираются вместе с файлами.

Удаление ставит deleted_at; такие записи не видны нигде, кроме корзины. Проект
«Кода» уходит в корзину вместе со своими чатами (с одной отметкой времени —
по ней они и восстанавливаются вместе). Чат Дизайна/OSINT/Пентеста забирает с
собой свою рабочую папку, если она больше никому не нужна.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.models.chat import Chat
from app.models.design import Design
from app.models.user import Project, Workspace

TRASH_DAYS = 7
PURGE_EVERY = 3600
CHAT_WORKSPACE = "chat_workspace"
logger = logging.getLogger("layla.trash")


def now() -> datetime:
    return datetime.now(UTC)


def _disposable_dir(project: Project, workspace: Workspace | None) -> Path | None:
    """Каталог проекта, который безопасно стереть: строго внутри каталога проектов."""
    if not project.path:
        return None
    base = Path((workspace.projects_dir if workspace else None) or get_settings().projects_dir).resolve()
    target = Path(project.path).resolve()
    if target == base or base not in target.parents:
        return None
    return target


async def purge_dirs(folders: list[Path]) -> None:
    for folder in folders:
        await run_in_threadpool(shutil.rmtree, folder, True)


async def _own_workspace(session: AsyncSession, chat: Chat, *, live_only: bool) -> Project | None:
    """Рабочая папка чата, если она только его: не проект «Кода», без других чатов и макетов."""
    if not chat.project_id:
        return None
    project = await session.get(Project, chat.project_id)
    if project is None or project.kind != CHAT_WORKSPACE:
        return None
    others = select(Chat.id).where(Chat.project_id == project.id, Chat.id != chat.id)
    if live_only:
        others = others.where(Chat.deleted_at.is_(None))
    if await session.scalar(others.limit(1)) is not None:
        return None
    if await session.scalar(select(Design.id).where(Design.project_id == project.id).limit(1)) is not None:
        return None
    return project


# --- В корзину и обратно ------------------------------------------------------

async def trash_chat(session: AsyncSession, chat: Chat, when: datetime) -> None:
    chat.deleted_at = when
    workspace = await _own_workspace(session, chat, live_only=True)
    if workspace is not None:
        workspace.deleted_at = when


async def trash_project(session: AsyncSession, project: Project, when: datetime) -> None:
    project.deleted_at = when
    for chat in await session.scalars(
        select(Chat).where(Chat.project_id == project.id, Chat.deleted_at.is_(None))
    ):
        chat.deleted_at = when


async def restore_chat(session: AsyncSession, chat: Chat) -> None:
    if chat.project_id:
        project = await session.get(Project, chat.project_id)
        if project is not None and project.deleted_at is not None:
            if project.kind != CHAT_WORKSPACE:
                # Проект «Кода» восстанавливается целиком — из корзины, как проект.
                raise ValueError("Чат удалён вместе с проектом — восстановите проект")
            project.deleted_at = None
    chat.deleted_at = None


async def restore_project(session: AsyncSession, project: Project) -> None:
    stamp = project.deleted_at
    project.deleted_at = None
    for chat in await session.scalars(
        select(Chat).where(Chat.project_id == project.id, Chat.deleted_at == stamp)
    ):
        chat.deleted_at = None


# --- Окончательное удаление ---------------------------------------------------

async def remove_project(session: AsyncSession, project: Project) -> Path | None:
    """Стереть проект вместе с его чатами. Возвращает каталог — удалить после commit."""
    workspace = await session.get(Workspace, project.workspace_id)
    folder = _disposable_dir(project, workspace)
    for chat in list(await session.scalars(select(Chat).where(Chat.project_id == project.id))):
        await session.delete(chat)
    await session.delete(project)
    return folder


async def remove_chat(session: AsyncSession, chat: Chat) -> Path | None:
    workspace = await _own_workspace(session, chat, live_only=False)
    if workspace is not None:
        return await remove_project(session, workspace)
    await session.delete(chat)
    return None


async def purge(maker, older_than: timedelta = timedelta(days=TRASH_DAYS)) -> int:
    """Стереть окончательно всё, что лежит в корзине дольше срока."""
    edge = now() - older_than
    folders: list[Path] = []
    removed = 0
    async with maker() as session:
        for project in list(await session.scalars(
            select(Project).where(Project.deleted_at.is_not(None), Project.deleted_at < edge)
        )):
            folder = await remove_project(session, project)
            removed += 1
            if folder:
                folders.append(folder)
        await session.flush()
        for chat in list(await session.scalars(
            select(Chat).where(Chat.deleted_at.is_not(None), Chat.deleted_at < edge)
        )):
            folder = await remove_chat(session, chat)
            removed += 1
            if folder:
                folders.append(folder)
        await session.commit()
    await purge_dirs(folders)
    return removed


async def purge_forever(maker) -> None:
    """Фоновая автоочистка корзины: при старте и затем раз в час."""
    while True:
        try:
            removed = await purge(maker)
            if removed:
                logger.info("Корзина: окончательно удалено записей: %s", removed)
        except Exception:  # noqa: BLE001 — очистка не должна ронять сервер
            logger.exception("Не удалось очистить корзину")
        await asyncio.sleep(PURGE_EVERY)
