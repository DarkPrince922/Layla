"""Проекты, импорт репозиториев и файловое дерево (спец. §5.7)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.db import get_session, get_sessionmaker
from app.models.chat import Chat
from app.models.job import Job
from app.models.user import Project, User, Workspace
from app.schemas.project import (
    FileChange,
    FileContent,
    FileNode,
    FileWrite,
    ProjectCreate,
    ProjectOut,
    RepoImport,
)
from app.services import audit, files, jobs, repo, sandbox, trash
from app.services.auth import get_current_user

router = APIRouter(prefix="/projects", tags=["projects"])


async def _default_workspace(session: AsyncSession, user: User) -> Workspace | None:
    return await session.scalar(select(Workspace).where(Workspace.owner_id == user.id).limit(1))


async def _owned_project(session: AsyncSession, user: User, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    # Проект в корзине недоступен нигде, кроме корзины.
    if project is None or project.deleted_at is not None:
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
    # Только проекты «Кода»: рабочие папки чатов и корзина сюда не попадают.
    rows = await session.scalars(
        select(Project).where(
            Project.workspace_id.in_(ws_ids),
            Project.kind == "project",
            Project.deleted_at.is_(None),
        ).order_by(Project.created_at)
    )
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

    # The client supplies a name, never a server path. UUIDs isolate workspaces
    # and also allow multiple projects with the same display name.
    project_id = str(uuid4())
    base = Path(ws.projects_dir or get_settings().projects_dir).resolve()
    dest = base / project_id
    await run_in_threadpool(lambda: dest.mkdir(parents=True, mode=0o755))
    project = Project(id=project_id, workspace_id=ws.id, name=body.name, path=str(dest))
    try:
        session.add(project)
        await audit.record(session, actor=user.id, action="project.create", target=project_id)
        await session.commit()
    except BaseException:
        dest.rmdir()  # only our newly created, empty directory
        raise
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
    dest = base / str(uuid4())
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


@contextmanager
def file_errors():
    try:
        yield
    except files.FileConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Файл уже существует") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Файл или каталог не найден") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail="Операция с файлом недоступна") from exc


@router.get("/{project_id}/files", response_model=list[FileNode])
async def project_files(
    project_id: str,
    path: str = Query(default=".", max_length=4096),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    project = await _owned_project(session, user, project_id)
    with file_errors():
        return await run_in_threadpool(files.list_dir, _project_root(project), path)


@router.get("/{project_id}/file", response_model=FileContent)
async def project_file(
    project_id: str,
    path: str = Query(..., min_length=1, max_length=4096),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await _owned_project(session, user, project_id)
    with file_errors():
        return await run_in_threadpool(files.read_file, _project_root(project), path)


@router.put("/{project_id}/file", response_model=FileChange)
async def write_project_file(
    project_id: str,
    body: FileWrite,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await _owned_project(session, user, project_id)
    with file_errors():
        change = await run_in_threadpool(
            files.change_file,
            _project_root(project),
            body.path,
            body.content,
            body.expected_sha256,
        )
    await audit.record(
        session,
        actor=user.id,
        action="project.file." + change["operation"],
        target=project.id,
        meta={"path": change["path"]},
    )
    await session.commit()
    sandbox.preview_touch(user.id, project.id)  # запущенное превью подхватит правку
    return change


@router.delete("/{project_id}/file", response_model=FileChange)
async def delete_project_file(
    project_id: str,
    path: str = Query(..., min_length=1, max_length=4096),
    expected_sha256: str = Query(..., pattern=r"^[a-f0-9]{64}$"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await _owned_project(session, user, project_id)
    with file_errors():
        change = await run_in_threadpool(
            files.change_file,
            _project_root(project),
            path,
            None,
            expected_sha256,
        )
    await audit.record(
        session,
        actor=user.id,
        action="project.file.delete",
        target=project.id,
        meta={"path": change["path"]},
    )
    await session.commit()
    sandbox.preview_touch(user.id, project.id)
    return change


@router.get("/{project_id}/archive")
async def project_archive(
    project_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    project = await _owned_project(session, user, project_id)
    with file_errors():
        archive = await run_in_threadpool(files.export_zip, _project_root(project))

    def chunks():
        try:
            while block := archive.read(64 * 1024):
                yield block
        finally:
            archive.close()

    filename = quote(project.name.replace("/", "-").replace("\\", "-") + ".zip", safe="")
    return StreamingResponse(
        chunks(),
        media_type="application/zip",
        background=BackgroundTask(archive.close),
        headers={
            "Content-Disposition": f"attachment; filename=project.zip; filename*=UTF-8''{filename}"
        },
    )


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    maker=Depends(get_sessionmaker),
) -> None:
    project = await _owned_project(session, user, project_id)
    active = list(await session.scalars(
        select(Job).join(Chat, Job.chat_id == Chat.id).where(
            Chat.project_id == project_id, Job.status.in_(("queued", "running"))
        )
    ))
    # Зависшую задачу удаление освобождает само; 409 только при реально живой.
    if await jobs.live_jobs(maker, active):
        raise HTTPException(status_code=409, detail="В проекте выполняется задача. Сначала остановите её.")
    # В корзину вместе с чатами; файлы на диске живут, пока корзину не очистят.
    await trash.trash_project(session, project, trash.now())
    await audit.record(session, actor=user.id, action="project.trash", target=project_id)
    await session.commit()


@router.post("/{project_id}/promote", response_model=ProjectOut)
async def promote_project(
    project_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Project:
    """Сделать рабочую папку чата проектом «Кода» («Открыть в Коде»).

    После этого папка видна в списке проектов и больше не удаляется вместе с чатом.
    """
    project = await _owned_project(session, user, project_id)
    if project.kind != "project":
        project.kind = "project"
        # Имя — по текущему названию чата (его могли переименовать после создания папки).
        chat = await session.scalar(select(Chat).where(Chat.project_id == project.id).limit(1))
        if chat is not None and chat.title and chat.title != "Новый чат":
            project.name = chat.title[:200]
        await audit.record(session, actor=user.id, action="project.promote", target=project_id)
        await session.commit()
    return project
