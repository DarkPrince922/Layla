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
from app.models.enums import Domain
from app.models.job import Job
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
    code_runner,
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
    "git_status": "Git: статус",
    "git_log": "Git: история",
    "git_diff": "Git: изменения",
    "git_commit": "Git: коммит",
    "git_push": "Git: пуш",
}

router = APIRouter(prefix="/chats", tags=["chats"])


async def _owned_chat(session: AsyncSession, user: User, chat_id: str) -> Chat:
    chat = await session.get(Chat, chat_id)
    # Чат в корзине недоступен нигде, кроме корзины.
    if chat is None or chat.owner_id != user.id or chat.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден")
    return chat


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
    active = list(await session.scalars(select(Job).where(Job.chat_id == chat_id, Job.status.in_(jobs.ACTIVE))))
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
                async for event in project_agent.run(
                    provider, key, model, payload, root, permissions,
                    extra=await run_in_threadpool(project_agent.project_rules, root),
                    runner=code_runner.Runner(user.id, chat.project_id, root),
                    git=await _git_agent(maker, user.id, root),
                ):
                    yield event
            else:
                async for kind, text in provider_client.stream_chat(provider, key, model, payload):
                    yield {"reasoning" if kind == "reasoning" else "delta": text}

        try:
            async for event in events():
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
        session.add(Checkpoint(chat_id=chat_id, message_id=message_id, path=point["path"],
                               content=point["content"], created_at=datetime.now(UTC)))
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
    for point in points:
        try:
            current = files.read_file(root, point.path)
        except FileNotFoundError:
            current = None
        if point.content is None:
            if current is not None:
                files.change_file(root, point.path, None, current["sha256"])
        elif current is None:
            files.change_file(root, point.path, point.content, None)
        elif current["content"] != point.content:
            files.change_file(root, point.path, point.content, current["sha256"])
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
        restored = await run_in_threadpool(_restore, _project_root(project), points)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"Не удалось вернуть файлы: {exc}") from exc
    rolled = 0
    for message in later:
        meta = dict(message.meta or {})
        if meta.get("checkpoint") and not meta.get("rolled_back"):
            meta["rolled_back"] = True
            message.meta = meta
            rolled += 1
    for point in points:
        await session.delete(point)
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
            extra = design_gen.DESIGN_CHAT_NOTE if domain == "design" else ""
            rules = await run_in_threadpool(project_agent.project_rules, root)
            extra = "\n\n".join(part for part in (extra, rules) if part)
            messages = payload
            if history.needed(messages, caps):
                # История не влезает в бюджет контекста — старое сворачивается в сводку,
                # а не отбрасывается. Не вышло — ниже просто уйдут последние сообщения.
                messages = await _compact_before_turn(h, maker, chat_id, prov, key, model, caps) or messages
            runner = code_runner.Runner(owner_id, project_id, root)
            git = await _git_agent(maker, owner_id, root)
            async for event in project_agent.run(prov, key, model, messages, root, permissions,
                                                 mode=mode, approve=approve, caps=caps, extra=extra,
                                                 runner=runner, git=git):
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
                        if ((tool["name"] in code_runner.RUN_TOOLS or tool["name"] in agent_git.GIT_MUTATING)
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
