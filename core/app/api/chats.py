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

from app.api.projects import _default_workspace, _owned_project, _project_root
from app.config import get_settings
from app.db import get_session, get_sessionmaker
from app.models.chat import Chat, Message
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
    SendMessageRequest,
)
from app.schemas.job import JobOut
from app.services import audit, jobs, project_agent, provider_client, trash
from app.services.auth import get_current_user


def _drop_tail(text: str, count: int) -> str:
    return text[: max(0, len(text) - count)]


_learned_label = project_agent.learned_label


_TOOL_LABELS = {
    "list_files": "Обзор папки",
    "read_file": "Чтение",
    "write_file": "Запись",
    "delete_file": "Удаление",
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
            # Все ключи провайдера: если один отклонён, ход повторится со следующим.
            key = await provider_client.key_ring(h.session, prov)
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

            caps = dict((prov.model_caps or {}).get(model) or {})
            async for event in project_agent.run(prov, key, model, payload, root, permissions,
                                                 mode=mode, approve=approve, caps=caps):
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
