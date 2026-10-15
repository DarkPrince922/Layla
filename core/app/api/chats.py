"""Чаты и стриминг ответов (спец. §5.2).

Ответ стримится от провайдера по SSE; кодовые чаты могут работать с файлами. Системный промпт берётся из выбранной
персоны. Оба сообщения (пользователя и ассистента) сохраняются в истории.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.concurrency import run_in_threadpool

from app.api.git import credential as git_credential
from app.api.projects import _default_workspace, _owned_project, _project_root
from app.config import get_settings
from app.db import get_session, get_sessionmaker
from app.models.chat import Chat, Checkpoint, Message
from app.models.code_session import CodeLeadEntry, CodeLeadTurn
from app.models.code_team import CodeTranscript
from app.models.code_worker import CodeWorker
from app.models.code_workspace import CodeWorkspaceCheckpoint
from app.models.enums import Domain
from app.models.job import Job
from app.models.mcp import McpServer
from app.models.persona import Persona
from app.models.provider import Provider
from app.models.user import Project, User, Workspace
from app.schemas.chat import (
    ChatCreate,
    ChatDetail,
    ChatOut,
    ChatRename,
    ClearChatsOut,
    MessageOut,
    RollbackOut,
    SendMessageRequest,
)
from app.schemas.job import JobOut
from app.services import (
    agent_git,
    audit,
    code_hooks,
    code_lead,
    code_lsp,
    code_mcp,
    code_runner,
    code_sessions,
    code_state,
    code_teams,
    code_workers,
    code_workspace,
    design_gen,
    files,
    history,
    jobs,
    project_agent,
    provider_client,
    sandbox,
    trash,
)
from app.services.auth import get_current_user


def _drop_tail(text: str, count: int) -> str:
    return text[: max(0, len(text) - count)]


_learned_label = project_agent.learned_label


_TOOL_LABELS = {
    "list_files": "Обзор папки",
    "read_file": "Чтение",
    "write_file": "Запись",
    "edit_file": "Правка",
    "append_file": "Дозапись",
    "delete_file": "Удаление",
    "run_command": "Команда",
    "run_code": "Запуск программы",
    "command_changes": "Изменения команды",
    "command_input": "Ввод в процесс",
    "git_status": "Git: статус",
    "git_log": "Git: история",
    "git_diff": "Git: изменения",
    "git_commit": "Git: коммит",
    "git_push": "Git: пуш",
}


async def _code_mcp(session: AsyncSession, owner_id: str, persona_id: str | None):
    rows = list(await session.scalars(select(McpServer).where(
        McpServer.owner_id == owner_id, McpServer.enabled.is_(True))))
    allowed = [row for row in rows if not row.personas or (persona_id and persona_id in row.personas)]
    return await code_mcp.McpAgent.create(allowed) if allowed else None

router = APIRouter(prefix="/chats", tags=["chats"])


async def _owned_chat(session: AsyncSession, user: User, chat_id: str) -> Chat:
    chat = await session.get(Chat, chat_id)
    # Чат в корзине недоступен нигде, кроме корзины.
    if chat is None or chat.owner_id != user.id or chat.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден")
    return chat


async def _worker_manager(session, maker, user, chat_id, *, active=True):
    chat = await _owned_chat(session, user, chat_id)
    if chat.domain != Domain.code or not chat.project_id:
        raise HTTPException(status_code=404, detail="Воркеры Code не найдены")
    project = await _owned_project(session, user, chat.project_id)
    permissions = None
    if chat.persona_id:
        persona = await session.get(Persona, chat.persona_id)
        permissions = persona.allowed_tools if persona is not None else []
    runtime = code_state.Runtime(maker, user.id, chat_id, chat.project_id)
    source = _project_root(project)
    root, workspace = source, None
    if active:
        try:
            root, workspace = await code_workspace.Runtime(runtime, source, permissions).current()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return code_workers.Manager(runtime, root, permissions, provider_id=chat.provider_id, model=chat.model,
                                source_root=source, workspace_id=workspace.id if workspace else None)


async def _lead_workspace(session, maker, user, chat_id):
    manager = await _worker_manager(session, maker, user, chat_id, active=False)
    return code_workspace.Runtime(manager.runtime, manager.root, manager.permissions,
                                  git=await _git_agent(maker, user.id, manager.root))


@router.get("/{chat_id}/workspaces")
async def get_code_workspaces(chat_id: str, user: User = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)) -> dict:
    workspace = await _lead_workspace(session, maker, user, chat_id)
    return await workspace.call("worktree_list", {})


@router.post("/{chat_id}/workspaces/{action}")
async def control_code_workspace(chat_id: str, action: str, body: dict, user: User = Depends(get_current_user),
                                 session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)) -> dict:
    name = {"enter": "enter_worktree", "resume": "resume_worktree", "exit": "exit_worktree", "publish": "worktree_publish"}.get(action)
    if not name:
        raise HTTPException(status_code=404, detail="Действие не найдено")
    workspace = await _lead_workspace(session, maker, user, chat_id)
    result = await workspace.call(name, body)
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@router.get("/{chat_id}/workspaces/{workspace_id}/diff")
async def get_workspace_diff(chat_id: str, workspace_id: str, user: User = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)) -> dict:
    workspace = await _lead_workspace(session, maker, user, chat_id)
    result = await workspace.call("worktree_diff", {"workspace_id": workspace_id})
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/{chat_id}/workspaces/{workspace_id}/files")
async def get_workspace_files(chat_id: str, workspace_id: str, path: str = ".", user: User = Depends(get_current_user),
                               session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)) -> dict:
    workspace = await _lead_workspace(session, maker, user, chat_id)
    if not workspace.tools():
        raise HTTPException(status_code=403, detail="Персона не разрешает чтение проекта")
    try:
        row = await workspace.owned(session, workspace_id)
        if row.status not in ("active", "kept"):
            raise ValueError("Рабочая копия недоступна")
        root = code_workspace.paths(workspace.root, workspace.runtime.project_id, row.id)[2]
        target = files.safe_join(root, path)
        return {"files": await run_in_threadpool(files.list_dir, root, path)} if target.is_dir() else await run_in_threadpool(files.read_file, root, path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{chat_id}/workspaces/{workspace_id}/archive")
async def download_code_workspace(chat_id: str, workspace_id: str, user: User = Depends(get_current_user),
                                   session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)):
    workspace = await _lead_workspace(session, maker, user, chat_id)
    if not workspace.tools():
        raise HTTPException(status_code=403, detail="Персона не разрешает чтение проекта")
    try:
        row = await workspace.owned(session, workspace_id)
        if row.status not in ("active", "kept"):
            raise ValueError("Рабочая копия недоступна")
        buffer = await run_in_threadpool(files.export_zip, code_workspace.paths(workspace.root, workspace.runtime.project_id, row.id)[2])
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    def chunks():
        try:
            while data := buffer.read(65536):
                yield data
        finally:
            buffer.close()
    return StreamingResponse(chunks(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{row.name}.zip"'})


@router.get("/{chat_id}/team")
async def get_code_team(chat_id: str, team_id: str | None = None, before: int | None = Query(None, ge=0),
                        user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                        maker=Depends(get_sessionmaker)) -> dict:
    manager = await _worker_manager(session, maker, user, chat_id, active=False)
    try:
        await manager.list()
        return await manager.team.info(team_id, before=before)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{chat_id}/session")
async def get_code_session(chat_id: str, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
                           user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                           maker=Depends(get_sessionmaker)) -> dict:
    manager = await _worker_manager(session, maker, user, chat_id, active=False)
    if manager.permissions is not None and "files.read" not in manager.permissions:
        raise HTTPException(status_code=403, detail="Персона не разрешает чтение сессии")
    rows = list((await session.execute(select(CodeLeadEntry, CodeLeadTurn).join(CodeLeadTurn).where(
        CodeLeadTurn.chat_id == chat_id, CodeLeadTurn.owner_id == user.id,
        CodeLeadTurn.project_id == manager.project_id
    ).order_by(CodeLeadTurn.created_at, CodeLeadTurn.id, CodeLeadEntry.sequence).offset(offset).limit(limit + 1))).all())
    return {"entries": [{"turn_id": turn.id, "sequence": entry.sequence, "active": turn.active,
                         "status": turn.status, "message": entry.message} for entry, turn in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None}


@router.post("/{chat_id}/team/create")
async def create_code_team(chat_id: str, body: code_teams.Create,
                           user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                           maker=Depends(get_sessionmaker)) -> dict:
    manager = await _worker_manager(session, maker, user, chat_id)
    result = await manager.team.call("team_create", body.model_dump())
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@router.post("/{chat_id}/team/message")
async def send_code_team_message(chat_id: str, body: code_teams.Send,
                                 user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                                 maker=Depends(get_sessionmaker)) -> dict:
    manager = await _worker_manager(session, maker, user, chat_id)
    result = await manager.team.call("send_message", body.model_dump())
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@router.post("/{chat_id}/team/close")
async def close_code_team(chat_id: str, user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)) -> dict:
    manager = await _worker_manager(session, maker, user, chat_id)
    result = await manager.team.call("team_delete", {})
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@router.get("/{chat_id}/workers/{worker_id}/transcript")
async def get_worker_transcript(chat_id: str, worker_id: str, offset: int = Query(0, ge=0),
                                limit: int = Query(50, ge=1, le=100), user: User = Depends(get_current_user),
                                session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)) -> dict:
    manager = await _worker_manager(session, maker, user, chat_id)
    try:
        await manager.owned(session, worker_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    rows = list(await session.scalars(select(CodeTranscript).where(CodeTranscript.worker_id == worker_id,
        CodeTranscript.sequence >= offset).order_by(CodeTranscript.sequence).limit(limit + 1)))
    return {"entries": [{"sequence": row.sequence, "message": row.message} for row in rows[:limit]],
            "next_offset": rows[limit].sequence if len(rows) > limit else None}


@router.get("/{chat_id}/workers/{worker_id}")
async def get_code_worker(chat_id: str, worker_id: str, offset: int = Query(0, ge=0),
                          limit: int = Query(12000, ge=1, le=40000), path: str | None = None,
                          user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                          maker=Depends(get_sessionmaker)) -> dict:
    manager = await _worker_manager(session, maker, user, chat_id)
    try:
        return await manager.output(code_workers.WorkerOutput(worker_id=worker_id, offset=offset, limit=limit, path=path))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{chat_id}/workers/{worker_id}/input")
async def input_code_worker(chat_id: str, worker_id: str, body: code_workers.CommandText,
                              user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                              maker=Depends(get_sessionmaker)) -> dict:
    manager = await _worker_manager(session, maker, user, chat_id)
    try:
        return await manager.input(code_workers.CommandInput(worker_id=worker_id, **body.model_dump()))
    except (ValueError, sandbox.SandboxError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{chat_id}/workers/{worker_id}/changes")
async def worker_shell_changes(chat_id: str, worker_id: str, body: code_runner.CommandChanges,
                                user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                                maker=Depends(get_sessionmaker)) -> dict:
    manager = await _worker_manager(session, maker, user, chat_id)
    try:
        return await manager.command_changes(code_workers.WorkerCommandChanges(worker_id=worker_id, **body.model_dump()))
    except (ValueError, OSError, sandbox.SandboxError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{chat_id}/workers/{worker_id}/{action}")
async def control_code_worker(chat_id: str, worker_id: str, action: str,
                              discard_changes: bool = False, user: User = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)) -> dict:
    manager = await _worker_manager(session, maker, user, chat_id)
    try:
        if action == "stop":
            return await manager.stop(worker_id)
        if action == "resume":
            return await manager.resume(worker_id)
        if action == "discard":
            return await manager.discard(code_workers.WorkerDiscard(worker_id=worker_id, discard_changes=discard_changes))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=404, detail="Неизвестное действие воркера")


@router.get("/{chat_id}/code-state")
async def get_code_state(
    chat_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    maker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> dict:
    chat = await _owned_chat(session, user, chat_id)
    if chat.domain != Domain.code or not chat.project_id:
        raise HTTPException(status_code=404, detail="Состояние Code не найдено")
    try:
        state = await code_state.Runtime(maker, user.id, chat.id, chat.project_id).read()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Состояние Code не найдено") from exc
    manager = await _worker_manager(session, maker, user, chat_id)
    return {**state, "tasks": code_state.visible_tasks(state), "workers": await manager.list()}


@router.post("/{chat_id}/code-state/answer")
async def answer_code_question(
    chat_id: str,
    body: code_state.QuestionAnswer,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    maker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> dict:
    chat = await _owned_chat(session, user, chat_id)
    if chat.domain != Domain.code or not chat.project_id:
        raise HTTPException(status_code=404, detail="Вопросы Code не найдены")
    active = await session.scalar(select(Job).where(Job.chat_id == chat_id, Job.status.in_(jobs.ACTIVE)))
    had_worker = active is not None and jobs.has_worker(active.id)
    runtime = code_state.Runtime(maker, user.id, chat.id, chat.project_id)
    outcome = await runtime.call("_question_answer", body.model_dump())
    if "error" in outcome:
        raise HTTPException(status_code=422, detail=outcome["error"])
    return {**outcome, "resume_needed": not had_worker}


@router.get("", response_model=list[ChatOut])
async def list_chats(
    project_id: str | None = None,
    domain: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Chat]:
    query = select(Chat).where(Chat.owner_id == user.id, Chat.deleted_at.is_(None))
    if q and q.strip():
        # Поиск по названию и по тексту сообщений.
        needle = f"%{q.strip()}%"
        in_messages = select(Message.id).where(Message.chat_id == Chat.id, Message.content.ilike(needle))
        query = query.where(or_(Chat.title.ilike(needle), in_messages.exists()))
    if project_id:
        await _owned_project(session, user, project_id)
        query = query.where(Chat.project_id == project_id)
    if domain:
        query = query.where(Chat.domain == domain)
    rows = await session.scalars(query.order_by(Chat.created_at.desc()))
    return list(rows)


@router.post("", response_model=ChatOut, status_code=201)
async def create_chat(
    body: ChatCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Chat:
    if body.persona_id:
        persona = await session.get(Persona, body.persona_id)
        if persona is None or (not persona.is_builtin and persona.owner_id != user.id):
            raise HTTPException(status_code=404, detail="Персона не найдена")
    project = None
    if body.project_id:
        project = await _owned_project(session, user, body.project_id)
        _project_root(project)
        if body.workspace_id and body.workspace_id != project.workspace_id:
            raise HTTPException(status_code=400, detail="Проект из другого рабочего пространства")
    if body.workspace_id and project is None:
        workspace = await session.get(Workspace, body.workspace_id)
        if workspace is None or workspace.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Рабочее пространство не найдено")
    provider_id = None
    if body.provider_id:
        provider = await session.get(Provider, body.provider_id)
        if provider is None or provider.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Провайдер не найден")
        provider_id = provider.id
    chat = Chat(
        owner_id=user.id,
        domain=body.domain,
        title=body.title or "Новый чат",
        persona_id=body.persona_id,
        model=body.model,
        provider_id=provider_id,
        workspace_id=project.workspace_id if project else body.workspace_id,
        project_id=project.id if project else None,
    )
    session.add(chat)
    await session.commit()
    return chat


@router.patch("/{chat_id}", response_model=ChatOut)
async def rename_chat(
    chat_id: str,
    body: ChatRename,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Chat:
    chat = await _owned_chat(session, user, chat_id)
    chat.title = body.title.strip()
    await session.commit()
    return chat


@router.get("/{chat_id}", response_model=ChatDetail)
async def get_chat(
    chat_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ChatDetail:
    chat = await _owned_chat(session, user, chat_id)
    msgs = await session.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    detail = ChatDetail.model_validate(chat)
    detail.messages = [MessageOut.model_validate(m) for m in msgs]
    last_job = await session.scalar(select(Job).where(Job.chat_id == chat_id).order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
    detail.last_job = JobOut.model_validate(last_job) if last_job else None
    return detail


@router.delete("/{chat_id}", status_code=204)
async def delete_chat(
    chat_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    maker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> None:
    chat = await _owned_chat(session, user, chat_id)
    worker_jobs = select(CodeWorker.job_id).where(CodeWorker.chat_id == chat_id)
    active = list(await session.scalars(select(Job).where(
        or_(Job.chat_id == chat_id, Job.id.in_(worker_jobs)), Job.status.in_(jobs.ACTIVE))))
    # Зависшую задачу (воркер умер) удаление освобождает само, а не упирается в 409.
    if await jobs.live_jobs(maker, active):
        raise HTTPException(status_code=409, detail="Сначала остановите задачу этого чата")
    # В корзину: 7 дней можно восстановить, потом чат и его папка стираются.
    await trash.trash_chat(session, chat, trash.now())
    await session.commit()


@router.delete("", response_model=ClearChatsOut)
async def clear_chats(
    domain: Domain,
    project_id: str | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    maker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> ClearChatsOut:
    """Очистить историю раздела (или проекта в «Коде»). Работающие чаты не трогаем."""
    query = select(Chat).where(Chat.owner_id == user.id, Chat.domain == domain, Chat.deleted_at.is_(None))
    if project_id:
        await _owned_project(session, user, project_id)
        query = query.where(Chat.project_id == project_id)
    active = list(await session.scalars(
        select(Job).where(Job.owner_id == user.id, Job.chat_id.is_not(None), Job.status.in_(jobs.ACTIVE))
    ))
    busy = {job.chat_id for job in await jobs.live_jobs(maker, active)}
    child_rows = list(await session.scalars(select(CodeWorker).where(
        CodeWorker.owner_id == user.id, CodeWorker.status.in_(code_workers.ACTIVE))))
    if child_rows:
        children = list(await session.scalars(select(Job).where(
            Job.id.in_([row.job_id for row in child_rows]), Job.status.in_(jobs.ACTIVE))))
        live = {job.id for job in await jobs.live_jobs(maker, children)}
        busy |= {row.chat_id for row in child_rows if row.job_id in live}
    deleted, skipped, when = 0, 0, trash.now()
    for chat in list(await session.scalars(query)):
        if chat.id in busy:
            skipped += 1
            continue
        await trash.trash_chat(session, chat, when)
        deleted += 1
    await audit.record(session, actor=user.id, action="chat.clear", target=domain.value,
                       meta={"deleted": deleted, "skipped": skipped})
    await session.commit()
    return ClearChatsOut(deleted=deleted, skipped=skipped)


async def _build_messages(session: AsyncSession, chat: Chat) -> list[dict[str, str]]:
    """Payload сообщений: инструкции персоны, сводка сжатой истории и свежие сообщения."""
    return await history.build(session, chat)


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: str,
    body: SendMessageRequest,
    user: User = Depends(get_current_user),
    maker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> StreamingResponse:
    """Принять сообщение пользователя и стримить ответ ассистента (SSE).

    Использует собственную сессию (из фабрики), живущую весь стрим, чтобы
    корректно записать оба сообщения.
    """
    # Подготовка вне стрима: проверка прав, модель, провайдер, ключ.
    async with maker() as session:
        chat = await _owned_chat(session, user, chat_id)
        if chat.domain == Domain.code and (user.id, chat_id) in code_lead.ACTIVE_CHATS:
            raise HTTPException(status_code=409, detail="В этом Code-чате уже выполняется задача")
        root = None
        permissions = None
        if chat.persona_id:
            persona = await session.get(Persona, chat.persona_id)
            if persona is None or (not persona.is_builtin and persona.owner_id != user.id):
                raise HTTPException(status_code=404, detail="Персона не найдена")
            permissions = persona.allowed_tools or []
        if chat.project_id:
            project = await _owned_project(session, user, chat.project_id)
            root = _project_root(project)
        mcp = await _code_mcp(session, user.id, chat.persona_id) if root and chat.domain == Domain.code else None
        model = body.model or chat.model
        if not model:
            raise HTTPException(status_code=400, detail="Не выбрана модель")
        provider = await provider_client.resolve_provider(session, user.id, model)
        if provider is None:
            raise HTTPException(
                status_code=400,
                detail="Модель недоступна: нет активного провайдера. "
                "Добавьте провайдера и включите «активен» в настройках.",
            )
        key = await provider_client.pick_key(session, provider)
        input_message = Message(chat_id=chat_id, role="user", content=body.content, created_at=datetime.now(UTC))
        session.add(input_message)
        await session.commit()
        payload = await _build_messages(session, chat)
        assistant = Message(chat_id=chat_id, role="assistant", content="", meta={}, created_at=datetime.now(UTC))
        session.add(assistant)
        await session.commit()
        msg_id = assistant.id

    async def event_stream() -> AsyncIterator[bytes]:
        full: list[str] = []
        reasoning: list[str] = []
        tool_events: dict[str, dict] = {}
        stream_error = None
        completed = False
        checkpointed = False

        async def persist(change: dict | None = None):
            async with maker() as session:
                msg = await session.get(Message, msg_id)
                if msg is None:
                    return
                msg.content = "".join(full)
                msg.meta = {
                    "reasoning": "".join(reasoning),
                    "tools": list(tool_events.values()),
                    "error": stream_error,
                    "checkpoint": checkpointed,
                }
                if change:
                    await audit.record(
                        session,
                        actor=user.id,
                        action="project.agent." + change["operation"],
                        target=chat.project_id,
                        meta={"path": change["path"]},
                    )
                await session.commit()
            if change and chat.project_id:
                sandbox.preview_touch(user.id, chat.project_id)  # запущенное превью подхватит правку

        async def events():
            if root:
                runtime = code_state.Runtime(maker, user.id, chat.id, chat.project_id) if chat.domain == Domain.code else None
                workers = code_workers.Manager(runtime, root, permissions, provider_id=provider.id, model=model) if runtime else None
                transcript = code_sessions.LeadTranscript(runtime, msg_id, input_message.id, workers.team,
                                                         provider_id=provider.id, model=model) if runtime else None
                messages = await transcript.load(payload) if transcript else payload
                engine = code_lead.run if runtime is not None else project_agent.run
                async with aclosing(engine(
                    provider, key, model, messages, root, permissions,
                    extra=await run_in_threadpool(project_agent.project_rules, root),
                    runner=code_runner.Runner(user.id, chat.project_id, root),
                    git=await _git_agent(maker, user.id, root),
                    mcp=mcp,
                    runtime=runtime, workers=workers, transcript=transcript,
                    lsp=code_lsp.Client(user.id, chat.project_id, root) if chat.domain == Domain.code else None,
                    workflow=code_hooks.Runtime(code_runner.Runner(user.id, chat.project_id, root), permissions,
                                                session_id=chat.id) if chat.domain == Domain.code else None,
                )) as stream:
                    async for event in stream:
                        yield event
            else:
                async for kind, text in provider_client.stream_chat_with_retry(provider, key, model, payload):
                    yield {kind if kind in ("reasoning", "retract", "retry") else "delta": text}

        try:
            async with aclosing(events()) as stream:
                async for event in stream:
                    if "checkpoint" in event:
                        # Служебное событие: сохраняем до изменения файла, клиенту не отправляем.
                        await _save_checkpoint(maker, chat_id, msg_id, event["checkpoint"])
                        checkpointed = True
                        continue
                    if "delta" in event:
                        full.append(event["delta"])
                    elif "retract" in event:
                        full[:] = [_drop_tail("".join(full), event["retract"])]
                    elif "reasoning" in event:
                        reasoning.append(event["reasoning"])
                    elif "tool" in event:
                        tool = event["tool"]
                        tool_events[tool["id"]] = tool
                        if tool["status"] != "running":
                            # Persist each completed operation before acknowledging it over SSE.
                            await persist(tool.get("change"))
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
            completed = True
        except Exception as exc:  # noqa: BLE001 — SSE must terminate with a saved partial response
            stream_error = (
                str(exc)
                if isinstance(exc, RuntimeError)
                else "Не удалось завершить ответ провайдера. Попробуйте продолжить диалог."
            )
            yield f"data: {json.dumps({'error': stream_error}, ensure_ascii=False)}\n\n".encode()
        finally:
            # Client disconnects must not discard diffs for files already written.
            if not completed and stream_error is None:
                stream_error = "Ответ остановлен; выполненные изменения сохранены."
            for event_id, event in list(tool_events.items()):
                if event["status"] == "running":
                    tool_events[event_id] = {
                        **event,
                        "status": "error",
                        "error": "Вызов прерван. Проверьте файл перед продолжением.",
                    }
            with anyio.CancelScope(shield=True):
                await persist()
        yield f"data: {json.dumps({'done': True, 'message_id': msg_id})}\n\n".encode()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


async def _git_agent(maker, owner_id: str, root: str) -> agent_git.GitAgent:
    """Git проекта от имени пользователя: он — автор коммитов, его токены — для пуша."""
    async with maker() as session:
        user = await session.get(User, owner_id)
        author = (user.display_name or user.email.split("@")[0]) if user else "Layla"
        email = user.email if user else "layla@localhost"

    async def credential(url: str | None):
        async with maker() as session:
            return await git_credential(session, owner_id, url)

    return agent_git.GitAgent(root, author, email, credential)


async def _audit_run(maker, owner_id: str, project_id: str, tool: dict) -> None:
    """Каждый запуск кода и git-действие агента — в аудит: что выполнено и с каким итогом."""
    action = "project.agent.git" if tool["name"] in agent_git.GIT_TOOLS else "project.agent.run"
    async with maker() as session:
        await audit.record(session, actor=owner_id, action=action, target=project_id,
                           meta={"tool": tool["name"], "command": (tool.get("command") or "")[:500],
                                 "exit_code": tool.get("exit_code"), "error": tool.get("error")})
        await session.commit()


async def _save_checkpoint(maker, chat_id: str, message_id: str, point: dict) -> None:
    """Исходное состояние файла — до того, как агент его изменит (для отката)."""
    async with maker() as session:
        checkpoint = Checkpoint(chat_id=chat_id, message_id=message_id, path=point["path"],
                               content=point["content"], encoding=point.get("encoding", "utf-8"),
                               mode=point.get("mode"), created_at=datetime.now(UTC))
        session.add(checkpoint)
        if point.get("workspace_id"):
            await session.flush()
            session.add(CodeWorkspaceCheckpoint(checkpoint_id=checkpoint.id, workspace_id=point["workspace_id"]))
        await session.commit()


async def _step_for(h: jobs.JobHandle, kind: str, value) -> None:
    """Шаги «В работе» для служебных запросов к модели (сводка контекста)."""
    if kind == "retry":
        await h.step(f"Нет связи с моделью — повтор {value['attempt']} из {value['max']} через {value['delay']:g} с")
    elif kind == "key":
        await h.step(project_agent.key_label(value))


async def _compact_before_turn(h: jobs.JobHandle, maker, chat_id: str, prov, key, model: str,
                               caps: dict) -> list[dict] | None:
    await h.step("История длиннее контекста модели — сворачиваю старые сообщения в сводку")
    try:
        async with maker() as session:
            chat = await session.get(Chat, chat_id)
            summary = await history.compact(session, chat, prov, key, model, caps,
                                            on_event=lambda kind, value: _step_for(h, kind, value))
            if summary is None:
                return None
            await h.step(f"Контекст сжат: {summary.meta['count']} сообщений в сводке, свежие — целиком")
            return await history.build(session, chat)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — сводка не получилась, но ход важнее
        await h.step("Не удалось сжать контекст — отправляю последние сообщения")
        return None


async def _chat_provider(session: AsyncSession, chat: Chat, user: User) -> Provider:
    provider = None
    if chat.provider_id:
        provider = await session.get(Provider, chat.provider_id)
        if provider is not None and not (provider.enabled and provider.active):
            provider = None
    if provider is None and chat.model:
        provider = await provider_client.resolve_provider(session, user.id, chat.model)
    if provider is None or not chat.model:
        raise HTTPException(status_code=400, detail="Не выбрана модель: отправьте сообщение с выбранной моделью.")
    return provider


def _restore(root: str, points: list[Checkpoint]) -> list[str]:
    """Вернуть файлы к сохранённому состоянию: от новых точек к старым."""
    restored = []
    from app.services import code_shell
    for point in points:
        code_shell.restore(root, {"path": point.path, "content": point.content,
                                  "encoding": point.encoding, "mode": point.mode})
        restored.append(point.path)
    return restored


@router.post("/{chat_id}/messages/{message_id}/rollback", response_model=RollbackOut)
async def rollback_to(
    chat_id: str,
    message_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RollbackOut:
    """«Откатить к этой точке»: файлы — как до этого ответа агента (и всех следующих)."""
    chat = await _owned_chat(session, user, chat_id)
    active = await session.scalar(select(Job).where(Job.chat_id == chat_id, Job.status.in_(jobs.ACTIVE)))
    if active is not None and not jobs.is_orphaned(active):
        raise HTTPException(status_code=409, detail="Дождитесь окончания задачи в этом чате.")
    rows = await history.messages(session, chat_id)
    index = next((i for i, m in enumerate(rows) if m.id == message_id and m.role == "assistant"), None)
    if index is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    later = rows[index:]
    points = list(await session.scalars(
        select(Checkpoint).where(Checkpoint.message_id.in_([m.id for m in later]))
        .order_by(Checkpoint.created_at.desc())))
    if not points or not chat.project_id:
        raise HTTPException(status_code=400, detail="Для этого ответа нет контрольной точки.")
    project = await _owned_project(session, user, chat.project_id)
    try:
        locations = dict((await session.execute(select(CodeWorkspaceCheckpoint.checkpoint_id,
            CodeWorkspaceCheckpoint.workspace_id).where(CodeWorkspaceCheckpoint.checkpoint_id.in_([p.id for p in points])))).all())
        bindings = code_workspace.Runtime(code_state.Runtime(None, user.id, chat_id, chat.project_id), _project_root(project))
        grouped = {}
        for point in points:
            root = _project_root(project)
            if workspace_id := locations.get(point.id):
                workspace = await bindings.owned(session, workspace_id)
                if workspace.status not in ("active", "kept"):
                    raise ValueError("Рабочая копия контрольной точки удалена; исходный проект не подставляется вместо неё")
                root = str(code_workspace.paths(root, project.id, workspace.id)[2])
                if not Path(root).is_dir():
                    raise ValueError("Рабочая копия контрольной точки отсутствует")
            grouped.setdefault(root, []).append(point)
        restored = []
        for root, group in grouped.items():
            restored.extend(await run_in_threadpool(_restore, root, group))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"Не удалось вернуть файлы: {exc}") from exc
    rolled = 0
    for message in later:
        meta = dict(message.meta or {})
        if chat.domain == Domain.code and history.is_summary(message):
            message.meta = {**meta, "disabled": True, "rolled_back": True}
        if meta.get("checkpoint") and not meta.get("rolled_back"):
            meta["rolled_back"] = True
            message.meta = meta
            rolled += 1
    for point in points:
        await session.delete(point)
    if chat.domain == Domain.code:
        await code_sessions.invalidate(session, chat_id, [message.id for message in later])
    paths = sorted(set(restored))
    await audit.record(session, actor=user.id, action="chat.rollback", target=chat_id,
                       meta={"message_id": message_id, "paths": paths})
    await session.commit()
    sandbox.preview_touch(user.id, project.id)
    return RollbackOut(restored=paths, messages=rolled)


@router.post("/{chat_id}/compact", response_model=JobOut, status_code=202)
async def compact_chat(
    chat_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    maker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> Job:
    """«Сжать контекст»: старые сообщения — в сводку, последние остаются как есть."""
    chat = await _owned_chat(session, user, chat_id)
    active = await session.scalar(select(Job).where(Job.chat_id == chat_id, Job.status.in_(jobs.ACTIVE)))
    if active is not None and not jobs.is_orphaned(active):
        raise HTTPException(status_code=409, detail="В этом чате уже выполняется задача.")
    provider = await _chat_provider(session, chat, user)
    provider_id, model = provider.id, chat.model
    job = Job(owner_id=user.id, domain=chat.domain.value, kind="chat.compact", title="Сжатие контекста",
              chat_id=chat_id, status="queued", created_at=datetime.now(UTC), result={"chat_id": chat_id})
    session.add(job)
    try:
        await session.commit()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="В этом чате уже выполняется задача.") from exc
    await session.refresh(job)

    async def worker(h: jobs.JobHandle) -> None:
        prov = await h.session.get(Provider, provider_id)
        key = await provider_client.key_ring(h.session, prov)
        caps = dict((prov.model_caps or {}).get(model) or {})
        await h.step("Сворачиваю старые сообщения в сводку", progress=0.1)
        async with maker() as work:
            row = await work.get(Chat, chat_id)
            summary = await history.compact(work, row, prov, key, model, caps, keep=4,
                                            on_event=lambda kind, value: _step_for(h, kind, value))
        if summary is None:
            await h.step("Сжимать нечего: разговор и так короткий", progress=1.0)
            return
        await h.set_result({"summary_id": summary.id, "count": summary.meta["count"]})
        await h.step(f"Контекст сжат: {summary.meta['count']} сообщений в сводке", progress=1.0)

    jobs.launch(maker, job.id, worker)
    return job


@router.post("/{chat_id}/run", response_model=JobOut, status_code=202)
async def run_chat(
    chat_id: str,
    body: SendMessageRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    maker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> Job:
    """One turn per chat, independent chats in parallel; all work is server-owned."""
    chat = await session.scalar(select(Chat).where(Chat.id == chat_id, Chat.owner_id == user.id).with_for_update())
    if chat is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    if body.request_id:
        previous = await session.scalar(select(Job).where(Job.chat_id == chat_id, Job.request_id == body.request_id))
        if previous:
            return previous
    if chat.domain == Domain.code and (user.id, chat_id) in code_lead.ACTIVE_CHATS:
        raise HTTPException(status_code=409, detail="В этом Code-чате уже выполняется задача")
    active = await session.scalar(select(Job).where(Job.chat_id == chat_id, Job.status.in_(jobs.ACTIVE)))
    if active is not None and jobs.is_orphaned(active):
        # Задача числится активной, но её воркер умер — не держим чат запертым.
        await jobs.release_orphan(maker, active.id)
        active = None
    if active is not None:
        raise HTTPException(status_code=409, detail="В этом чате уже выполняется задача. Можно открыть другой чат.")
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="Сообщение не должно быть пустым")
    permissions = None
    if chat.persona_id:
        persona = await session.get(Persona, chat.persona_id)
        if persona is None or (not persona.is_builtin and persona.owner_id != user.id):
            raise HTTPException(status_code=404, detail="Персона не найдена")
        permissions = persona.allowed_tools or []
    model = body.model or chat.model
    if not model:
        raise HTTPException(status_code=400, detail="Не выбрана модель")
    # Выбор пользователя закрепляется за чатом: иначе при следующем открытии
    # подставлялась бы исходная модель чата и выбор «не держался».
    model_changed = bool(body.model and body.model != chat.model)
    if model_changed:
        chat.model = body.model
    # Провайдер, указанный явно в пикере, важнее угадывания по имени модели:
    # одно и то же имя может быть у нескольких провайдеров.
    provider = None
    if body.provider_id:
        provider = await session.get(Provider, body.provider_id)
        if provider is None or provider.owner_id != user.id or not (provider.enabled and provider.active):
            raise HTTPException(status_code=400, detail="Выбранный провайдер недоступен")
    elif chat.provider_id and not model_changed:
        # Без явного выбора — провайдер, уже закреплённый за чатом (если он ещё работает).
        saved = await session.get(Provider, chat.provider_id)
        if saved is not None and saved.enabled and saved.active:
            provider = saved
    if provider is None:
        provider = await provider_client.resolve_provider(session, user.id, model)
    if provider is None:
        raise HTTPException(status_code=400, detail="Модель недоступна: нет активного провайдера.")
    provider_id = provider.id
    # Провайдер закрепляется за чатом, как и модель: иначе при одинаковых именах
    # моделей у разных провайдеров после перезагрузки запрос ушёл бы к первому.
    chat.provider_id = provider_id
    owner_id = user.id
    domain = chat.domain.value
    persona_id = chat.persona_id
    created_root = None
    if chat.project_id:
        project = await _owned_project(session, user, chat.project_id)
    else:
        # OSINT, Design and Pentest also need an actual isolated output directory.
        workspace = await _default_workspace(session, user)
        if workspace is None:
            raise HTTPException(status_code=400, detail="Нет рабочего пространства")
        project_id = str(uuid4())
        base = Path(workspace.projects_dir or get_settings().projects_dir).resolve()
        created_root = base / project_id
        await run_in_threadpool(lambda: created_root.mkdir(parents=True, mode=0o755))
        project = Project(id=project_id, workspace_id=workspace.id, kind=trash.CHAT_WORKSPACE,
                          name=f"{domain.upper()} · {chat.title or body.content[:60]}"[:200], path=str(created_root))
        session.add(project)
        # Сначала INSERT проекта, потом ссылка на него: без связи в ORM Postgres
        # иначе получал UPDATE chats раньше INSERT projects и отклонял внешний ключ.
        await session.flush()
        chat.project_id = project_id
        chat.workspace_id = workspace.id
    root = _project_root(project)
    project_id = project.id
    mode = body.mode
    try:
        chat.model = model
        if not chat.title or chat.title == "Новый чат":
            chat.title = body.content[:80]
        user_message = Message(chat_id=chat_id, role="user", content=body.content,
                               created_at=datetime.now(UTC))
        session.add(user_message)
        await session.flush()
        payload = await _build_messages(session, chat)
        assistant = Message(chat_id=chat_id, role="assistant", content="", meta={},
                            created_at=datetime.now(UTC))
        session.add(assistant)
        await session.flush()
        msg_id = assistant.id
        job = Job(owner_id=owner_id, domain=domain, kind="project.agent", title=body.content[:80],
                  chat_id=chat_id, request_id=body.request_id, status="queued",
                  created_at=datetime.now(UTC),
                  result={"chat_id": chat_id, "message_id": msg_id, "project_id": project_id,
                          "mode": mode})
        session.add(job)
        await session.commit()
        await session.refresh(job)
    except BaseException as exc:
        await session.rollback()
        if created_root:
            with suppress(OSError):
                created_root.rmdir()
        if isinstance(exc, IntegrityError):
            if body.request_id:
                previous = await session.scalar(select(Job).where(Job.chat_id == chat_id, Job.request_id == body.request_id))
                if previous:
                    return previous
            raise HTTPException(status_code=409, detail="В этом чате уже выполняется задача") from exc
        raise

    async def worker(h: jobs.JobHandle) -> None:
        full: list[str] = []
        reasoning: list[str] = []
        tools: dict[str, dict] = {}
        error = None
        last_persist = 0.0
        checkpointed = False  # есть контрольная точка — ход можно откатить
        todos: list[dict] = []  # план агента (update_todos) — чеклист в сообщении

        async def persist(change: dict | None = None) -> None:
            nonlocal last_persist
            async with maker() as output_session:
                msg = await output_session.get(Message, msg_id)
                if msg is None:
                    return
                msg.content = "".join(full)
                msg.meta = {"reasoning": "".join(reasoning), "tools": list(tools.values()), "error": error,
                            "mode": mode, "checkpoint": checkpointed, "todos": todos}
                if change:
                    await audit.record(output_session, actor=owner_id,
                                       action="project.agent." + change["operation"],
                                       target=project_id, meta={"path": change["path"]})
                await output_session.commit()
            last_persist = time.monotonic()

        try:
            prov = await h.session.get(Provider, provider_id)
            if prov is None or not prov.enabled or not prov.active:
                raise RuntimeError("Провайдер отключён или удалён. Выберите другую модель.")
            # Все ключи провайдера: если один отклонён, ход повторится со следующим.
            key = await provider_client.key_ring(h.session, prov)
            await h.step("Модель обрабатывает запрос", progress=0.05)
            async def approve(pending: dict) -> str:
                """Режим «С подтверждением»: ждём решения пользователя по изменению."""
                label = _TOOL_LABELS.get(pending["name"], pending["name"])
                target = pending.get("path") or pending.get("command", "")[:120]
                await h.set_result({"approval": {"id": pending["id"], "name": pending["name"],
                                                 "path": pending.get("path", ""),
                                                 "command": pending.get("command", "")}})
                await h.step(f"Ждёт подтверждения — {label}: {target}")
                try:
                    return await jobs.wait_decision(h.job.id, pending["id"])
                finally:
                    await h.set_result({"approval": None})

            caps = dict((prov.model_caps or {}).get(model) or {})
            # Указания домена и правила проекта (LAYLA.md) — дополнение к системному промпту.
            from app.services.pentest_policy import PENTEST_CHAT_NOTE
            extra = design_gen.DESIGN_CHAT_NOTE if domain == "design" else PENTEST_CHAT_NOTE if domain == "pentest" else ""
            rules = await run_in_threadpool(project_agent.project_rules, root)
            extra = "\n\n".join(part for part in (extra, rules) if part)
            messages = payload
            if history.needed(messages, caps):
                # История не влезает в бюджет контекста — старое сворачивается в сводку,
                # а не отбрасывается. Не вышло — ниже просто уйдут последние сообщения.
                messages = await _compact_before_turn(h, maker, chat_id, prov, key, model, caps) or messages
            runner = code_runner.Runner(owner_id, project_id, root)
            git = await _git_agent(maker, owner_id, root)
            mcp = await _code_mcp(h.session, owner_id, persona_id) if domain == "code" else None
            runtime = code_state.Runtime(maker, owner_id, chat_id, project_id) if domain == "code" else None
            workers = code_workers.Manager(runtime, root, permissions, provider_id=prov.id, model=model,
                                           mode=mode, caps=caps) if runtime is not None else None
            transcript = code_sessions.LeadTranscript(runtime, msg_id, user_message.id, workers.team,
                                                     provider_id=prov.id, model=model) if runtime else None
            if transcript is not None:
                messages = await transcript.load(messages)
            engine = code_lead.run if runtime is not None else project_agent.run
            async with aclosing(engine(prov, key, model, messages, root, permissions,
                                                 mode=mode, approve=approve, caps=caps, extra=extra,
                                                 runner=runner, git=git, mcp=mcp, runtime=runtime, workers=workers,
                                                 lsp=code_lsp.Client(owner_id, project_id, root) if domain == "code" else None,
                                                 workflow=code_hooks.Runtime(runner, permissions, mode=mode,
                                                                             session_id=chat_id) if domain == "code" else None,
                                                 transcript=transcript, **({"job_id": h.job.id} if runtime else {}))) as stream:
                async for event in stream:
                    if "workspace" in event:
                        # A control notification must not consume the first
                        # partial-answer flush; the provider may pause next.
                        await h.set_result({"workspace": event["workspace"]})
                        continue
                    if "delta" in event:
                        full.append(event["delta"])
                    elif "retract" in event:
                        # Ход повторяется — уже показанный кусок ответа убираем, чтобы не задвоился.
                        full[:] = [_drop_tail("".join(full), event["retract"])]
                        await persist()
                    elif "trimmed" in event:
                        await h.step(f"История длиннее контекста модели — {event['trimmed']} старых "
                                     "сообщений не отправлено модели (в чате они остались)")
                    elif "retry" in event:
                        info = event["retry"]
                        await h.step(f"Нет связи с моделью — повтор {info['attempt']} из {info['max']} "
                                     f"через {info['delay']:g} с")
                    elif "key" in event:
                        await h.step(project_agent.key_label(event["key"]))
                    elif "cut" in event:
                        await h.step(project_agent.cut_label(event["cut"]))
                    elif "todos" in event:
                        todos[:] = event["todos"]
                        current = next((t["content"] for t in todos if t["status"] == "in_progress"), None)
                        done = sum(t["status"] == "done" for t in todos)
                        await h.step(f"План: {done} из {len(todos)}" + (f" · {current}" if current else ""))
                        await persist()
                    elif "overthink" in event:
                        await h.step(project_agent.overthink_label(event["overthink"]))
                    elif "checkpoint" in event:
                        await _save_checkpoint(maker, chat_id, msg_id, event["checkpoint"])
                        if not checkpointed:
                            checkpointed = True
                            await persist()
                    elif "shrunk" in event:
                        await h.step(f"Длинная задача: {event['shrunk']} старых результатов инструментов "
                                     "ужато, чтобы не упереться в контекст")
                    elif "learned" in event:
                        # Запоминаем, чего модель не умеет, чтобы дальше сразу слать правильный запрос.
                        known = dict(prov.model_caps or {})
                        known[model] = {**(known.get(model) or {}), **event["learned"]}
                        prov.model_caps = known
                        await h.session.commit()
                        await h.step(_learned_label(event["learned"]))
                    elif "reasoning" in event:
                        reasoning.append(event["reasoning"])
                        await h.reason(event["reasoning"])
                    elif "tool" in event:
                        tool = event["tool"]
                        previous = tools.get(tool["id"])
                        tools[tool["id"]] = tool
                        if previous is not None and previous["status"] == tool["status"] == "running":
                            # Живой вывод команды: шаг не повторяем, сохраняется ниже не чаще раза в 0.7 с.
                            pass
                        else:
                            label = _TOOL_LABELS.get(tool["name"], tool["name"])
                            target = tool.get("path") or tool.get("command", "")[:120]
                            suffix = {"error": " — ошибка", "done": " — готово", "rejected": " — отклонено"}.get(tool["status"], "")
                            if tool["name"] in code_runner.RUN_TOOLS and tool["status"] == "done":
                                suffix = f" — код выхода {tool.get('exit_code')}"
                            if tool["status"] != "pending":
                                await h.step(f"{label}: {target}{suffix}")
                            # В аудит — только применённые изменения, не превью на подтверждение.
                            await persist(tool.get("change") if tool["status"] == "done" else None)
                            if tool.get("change") and tool["status"] == "done":
                                sandbox.preview_touch(owner_id, project_id)  # запущенное превью подхватит правку
                            if ((tool["name"] in (code_runner.RUN_TOOLS | code_runner.SHELL_TOOLS) or tool["name"] in agent_git.GIT_MUTATING or tool["name"] == "project_hook")
                                    and tool["status"] in ("done", "error")):
                                await _audit_run(maker, owner_id, project_id, tool)
                    if time.monotonic() - last_persist >= 0.7:
                        await persist()
            await h.step("Ответ сохранён", progress=1.0)
        except asyncio.CancelledError:
            error = "Задача остановлена. Выполненные изменения сохранены."
            raise
        except Exception as exc:
            error = jobs.public_error(exc)
            raise
        finally:
            for tool_id, tool in list(tools.items()):
                if tool["status"] in ("running", "pending"):
                    # change у pending — это лишь превью, оно не было применено.
                    cleaned = {k: v for k, v in tool.items() if k != "change"}
                    tools[tool_id] = {**cleaned, "status": "error", "error": error or "Вызов прерван"}
            await persist()

    jobs.launch(maker, job.id, worker)
    return job
