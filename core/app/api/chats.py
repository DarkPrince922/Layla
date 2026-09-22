"""Чаты и стриминг ответов (спец. §5.2).

Ответ стримится от провайдера по SSE; кодовые чаты могут работать с файлами. Системный промпт берётся из выбранной
персоны. Оба сообщения (пользователя и ассистента) сохраняются в истории.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.concurrency import run_in_threadpool

from app.api.projects import (
    _default_workspace,
    _owned_project,
    _project_root,
    purge_dirs,
    remove_project,
)
from app.config import get_settings
from app.db import get_session, get_sessionmaker
from app.models.chat import Chat, Message
from app.models.design import Design
from app.models.enums import Domain
from app.models.job import Job
from app.models.persona import Persona
from app.models.provider import Provider
from app.models.user import Project, User, Workspace
from app.schemas.chat import (
    ChatCreate,
    ChatDetail,
    ChatOut,
    ClearChatsOut,
    MessageOut,
    SendMessageRequest,
)
from app.schemas.job import JobOut
from app.services import audit, jobs, project_agent, provider_client
from app.services.auth import get_current_user

_TOOL_LABELS = {
    "list_files": "Обзор папки",
    "read_file": "Чтение",
    "write_file": "Запись",
    "delete_file": "Удаление",
}

router = APIRouter(prefix="/chats", tags=["chats"])


async def _owned_chat(session: AsyncSession, user: User, chat_id: str) -> Chat:
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден")
    return chat


@router.get("", response_model=list[ChatOut])
async def list_chats(
    project_id: str | None = None,
    domain: str | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Chat]:
    query = select(Chat).where(Chat.owner_id == user.id)
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
    chat = Chat(
        owner_id=user.id,
        domain=body.domain,
        title=body.title or "Новый чат",
        persona_id=body.persona_id,
        model=body.model,
        workspace_id=project.workspace_id if project else body.workspace_id,
        project_id=project.id if project else None,
    )
    session.add(chat)
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
    active = list(await session.scalars(select(Job).where(Job.chat_id == chat_id, Job.status.in_(jobs.ACTIVE))))
    # Зависшую задачу (воркер умер) удаление освобождает само, а не упирается в 409.
    if await jobs.live_jobs(maker, active):
        raise HTTPException(status_code=409, detail="Сначала остановите задачу этого чата")
    folder = await _delete_chat(session, chat)
    await session.commit()
    if folder:
        await purge_dirs([folder])


@router.delete("", response_model=ClearChatsOut)
async def clear_chats(
    domain: Domain,
    project_id: str | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    maker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> ClearChatsOut:
    """Очистить историю раздела (или проекта в «Коде»). Работающие чаты не трогаем."""
    query = select(Chat).where(Chat.owner_id == user.id, Chat.domain == domain)
    if project_id:
        await _owned_project(session, user, project_id)
        query = query.where(Chat.project_id == project_id)
    active = list(await session.scalars(
        select(Job).where(Job.owner_id == user.id, Job.chat_id.is_not(None), Job.status.in_(jobs.ACTIVE))
    ))
    busy = {job.chat_id for job in await jobs.live_jobs(maker, active)}
    deleted, skipped, folders = 0, 0, []
    for chat in list(await session.scalars(query)):
        if chat.id in busy:
            skipped += 1
            continue
        folder = await _delete_chat(session, chat)
        deleted += 1
        if folder:
            folders.append(folder)
    await audit.record(session, actor=user.id, action="chat.clear", target=domain.value,
                       meta={"deleted": deleted, "skipped": skipped})
    await session.commit()
    await purge_dirs(folders)
    return ClearChatsOut(deleted=deleted, skipped=skipped)


async def _delete_chat(session: AsyncSession, chat: Chat) -> Path | None:
    """Удалить чат. Вне «Кода» у чата своя рабочая папка-проект: если она больше
    никому не нужна (нет других чатов и макетов), удаляем и её, иначе такие
    проекты копились бы в списке «Кода»."""
    if chat.project_id and chat.domain != Domain.code:
        project = await session.get(Project, chat.project_id)
        if project is not None and not project.repo_url:
            other = await session.scalar(
                select(Chat.id).where(Chat.project_id == project.id, Chat.id != chat.id).limit(1)
            )
            linked = await session.scalar(select(Design.id).where(Design.project_id == project.id).limit(1))
            if other is None and linked is None:
                return await remove_project(session, project)
    await session.delete(chat)
    return None


async def _build_messages(session: AsyncSession, chat: Chat) -> list[dict[str, str]]:
    """Собрать payload сообщений: системный промпт персоны + история."""
    out: list[dict[str, str]] = []
    if chat.persona_id:
        persona = await session.get(Persona, chat.persona_id)
        if persona and persona.instructions:
            out.append({"role": "system", "content": persona.instructions})
    history = await session.scalars(
        select(Message).where(Message.chat_id == chat.id).order_by(Message.created_at)
    )
    for m in history:
        if m.role in ("user", "assistant", "system"):
            content = m.content
            changes = [t["change"] for t in (m.meta or {}).get("tools", [])
                       if t.get("change") and t.get("status") == "done"]
            if changes:
                content += (
                    "\n[Applied project changes: "
                    + ", ".join(f"{c['operation']} {c['path']}" for c in changes)
                    + "]"
                )
            if content:
                entry = {"role": m.role, "content": content}
                if m.role == "assistant" and (m.meta or {}).get("reasoning"):
                    entry["reasoning_content"] = m.meta["reasoning"]
                out.append(entry)
    return out


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
        session.add(Message(chat_id=chat_id, role="user", content=body.content))
        await session.commit()
        payload = await _build_messages(session, chat)
        assistant = Message(chat_id=chat_id, role="assistant", content="", meta={})
        session.add(assistant)
        await session.commit()
        msg_id = assistant.id

    async def event_stream() -> AsyncIterator[bytes]:
        full: list[str] = []
        reasoning: list[str] = []
        tool_events: dict[str, dict] = {}
        stream_error = None
        completed = False

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

        async def events():
            if root:
                async for event in project_agent.run(
                    provider, key, model, payload, root, permissions
                ):
                    yield event
            else:
                async for kind, text in provider_client.stream_chat(provider, key, model, payload):
                    yield {"reasoning" if kind == "reasoning" else "delta": text}

        try:
            async for event in events():
                if "delta" in event:
                    full.append(event["delta"])
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
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    if body.model and body.model != chat.model:
        chat.model = body.model
    # Провайдер, указанный явно в пикере, важнее угадывания по имени модели:
    # одно и то же имя может быть у нескольких провайдеров.
    provider = None
    if body.provider_id:
        provider = await session.get(Provider, body.provider_id)
        if provider is None or provider.owner_id != user.id or not (provider.enabled and provider.active):
            raise HTTPException(status_code=400, detail="Выбранный провайдер недоступен")
    if provider is None:
        provider = await provider_client.resolve_provider(session, user.id, model)
    if provider is None:
        raise HTTPException(status_code=400, detail="Модель недоступна: нет активного провайдера.")
    provider_id = provider.id
    owner_id = user.id
    domain = chat.domain.value
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
        project = Project(id=project_id, workspace_id=workspace.id,
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

        async def persist(change: dict | None = None) -> None:
            nonlocal last_persist
            async with maker() as output_session:
                msg = await output_session.get(Message, msg_id)
                if msg is None:
                    return
                msg.content = "".join(full)
                msg.meta = {"reasoning": "".join(reasoning), "tools": list(tools.values()), "error": error,
                            "mode": mode}
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
            key = await provider_client.pick_key(h.session, prov)
            await h.step("Модель обрабатывает запрос", progress=0.05)
            async def approve(pending: dict) -> str:
                """Режим «С подтверждением»: ждём решения пользователя по изменению."""
                label = _TOOL_LABELS.get(pending["name"], pending["name"])
                await h.set_result({"approval": {"id": pending["id"], "name": pending["name"],
                                                 "path": pending.get("path", "")}})
                await h.step(f"Ждёт подтверждения — {label}: {pending.get('path', '')}")
                try:
                    return await jobs.wait_decision(h.job.id, pending["id"])
                finally:
                    await h.set_result({"approval": None})

            async for event in project_agent.run(prov, key, model, payload, root, permissions,
                                                 mode=mode, approve=approve):
                if "delta" in event:
                    full.append(event["delta"])
                elif "reasoning" in event:
                    reasoning.append(event["reasoning"])
                    await h.reason(event["reasoning"])
                elif "tool" in event:
                    tool = event["tool"]
                    tools[tool["id"]] = tool
                    label = _TOOL_LABELS.get(tool["name"], tool["name"])
                    suffix = {"error": " — ошибка", "done": " — готово", "rejected": " — отклонено"}.get(tool["status"], "")
                    if tool["status"] != "pending":
                        await h.step(f"{label}: {tool.get('path', '')}{suffix}")
                    # В аудит — только применённые изменения, не превью на подтверждение.
                    await persist(tool.get("change") if tool["status"] == "done" else None)
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
