"""Проекты, импорт репозиториев и файловое дерево (спец. §5.7)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.user import Project, User, Workspace
from app.schemas.project import (
    FileContent,
    FileNode,
    ProjectCreate,
    ProjectOut,
    RepoImport,
)
from app.services import files, repo
from app.services import audit
from app.services.auth import get_current_user

router = APIRouter(prefix="/projects", tags=["projects"])


async def _default_workspace(session: AsyncSession, user: User) -> Workspace | None:
    return await session.scalar(
        select(Workspace).where(Workspace.owner_id == user.id).limit(1)
    )


async def _owned_project(session: AsyncSession, user: User, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    ws = await session.get(Workspace, project.workspace_id)
    if ws is None or ws.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return project


def _project_root(project: Project) -> str:
    if not project.path:
        raise HTTPException(status_code=400, detail="У проекта нет каталога на диске")
    return project.path


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Project]:
    ws_ids = [
        w.id for w in await session.scalars(select(Workspace).where(Workspace.owner_id == user.id))
    ]
    if not ws_ids:
        return []
    rows = await session.scalars(select(Project).where(Project.workspace_id.in_(ws_ids)))
    return list(rows)


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Project:
    ws = None
    if body.workspace_id:
        ws = await session.get(Workspace, body.workspace_id)
        if ws is None or ws.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Рабочее пространство не найдено")
    else:
        ws = await _default_workspace(session, user)
    if ws is None:
        raise HTTPException(status_code=400, detail="Нет рабочего пространства")

    project = Project(workspace_id=ws.id, name=body.name, path=body.path)
    session.add(project)
    await session.commit()
    return project


@router.post("/import", response_model=ProjectOut, status_code=201)
async def import_repo(
    body: RepoImport,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Project:
    """Клонировать GitHub/git-репозиторий в каталог проектов и создать проект."""
    try:
        url = repo.validate_repo_url(body.repo_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ws = None
    if body.workspace_id:
        ws = await session.get(Workspace, body.workspace_id)
        if ws is None or ws.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Рабочее пространство не найдено")
    else:
        ws = await _default_workspace(session, user)
    if ws is None:
        raise HTTPException(status_code=400, detail="Нет рабочего пространства")

    settings = get_settings()
    base = Path(ws.projects_dir or settings.projects_dir).resolve()
    name = body.name or repo.safe_dir_name(url)
    dest = base / repo.safe_dir_name(name)
    if dest.exists():
        raise HTTPException(status_code=409, detail="Каталог назначения уже существует")

    try:
        await repo.clone(url, dest)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    project = Project(workspace_id=ws.id, name=name, repo_url=url, path=str(dest))
    session.add(project)
    await audit.record(
        session, actor=user.id, action="project.import", target=url, meta={"path": str(dest)}
    )
    await session.commit()
    return project


@router.get("/{project_id}/files", response_model=list[FileNode])
async def project_files(
    project_id: str,
    path: str = Query(default="."),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[FileNode]:
    project = await _owned_project(session, user, project_id)
    root = _project_root(project)
    try:
        return [FileNode(**e) for e in files.list_dir(root, path)]
    except ValueError:
        raise HTTPException(status_code=400, detail="Недопустимый путь")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Каталог не найден")


@router.get("/{project_id}/file", response_model=FileContent)
async def project_file(
    project_id: str,
    path: str = Query(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FileContent:
    project = await _owned_project(session, user, project_id)
    root = _project_root(project)
    try:
        return FileContent(path=path, content=files.read_text(root, path))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Файл не найден")


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    project = await _owned_project(session, user, project_id)
    await session.delete(project)
    await session.commit()
