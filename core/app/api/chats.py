"""Чаты и стриминг ответов (спец. §5.2).

Ответ стримится от провайдера по SSE; кодовые чаты могут работать с файлами. Системный промпт берётся из выбранной
персоны. Оба сообщения (пользователя и ассистента) сохраняются в истории.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.projects import _owned_project, _project_root
from app.db import get_session, get_sessionmaker
from app.models.chat import Chat, Message
from app.models.enums import Domain
from app.models.job import Job
from app.models.persona import Persona
from app.models.provider import Provider
from app.models.user import User
from app.schemas.chat import ChatCreate, ChatDetail, ChatOut, MessageOut, SendMessageRequest
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
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Chat]:
    query = select(Chat).where(Chat.owner_id == user.id)
    if project_id:
        await _owned_project(session, user, project_id)
        query = query.where(Chat.project_id == project_id)
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
        if body.domain != Domain.code:
            raise HTTPException(
                status_code=400, detail="Файловые инструменты доступны в разделе Код"
            )
        project = await _owned_project(session, user, body.project_id)
        _project_root(project)
        if body.workspace_id and body.workspace_id != project.workspace_id:
            raise HTTPException(status_code=400, detail="Проект из другого рабочего пространства")
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
    return detail


@router.delete("/{chat_id}", status_code=204)
async def delete_chat(
    chat_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    chat = await _owned_chat(session, user, chat_id)
    await session.delete(chat)
    await session.commit()


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
            changes = [t["change"] for t in (m.meta or {}).get("tools", []) if t.get("change")]
            if changes:
                content += (
                    "\n[Applied project changes: "
                    + ", ".join(f"{c['operation']} {c['path']}" for c in changes)
                    + "]"
                )
            if content:
                out.append({"role": m.role, "content": content})
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
            if chat.domain != Domain.code:
                raise HTTPException(
                    status_code=400, detail="Файловые инструменты доступны в разделе Код"
                )
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


@router.post("/{chat_id}/agent-run", response_model=JobOut, status_code=202)
async def agent_run_bg(
    chat_id: str,
    body: SendMessageRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    maker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> Job:
    """Запустить кодинг-агента в ФОНЕ для проектного чата.

    Возвращает задачу сразу; агент создаёт/меняет файлы проекта в фоне, а его
    шаги, размышление и дифы видны в панели «В работе». Ответ ассистента и
    изменения сохраняются в историю чата.
    """
    chat = await _owned_chat(session, user, chat_id)
    if not chat.project_id:
        raise HTTPException(status_code=400, detail="Фоновый агент доступен только в чате проекта")
    project = await _owned_project(session, user, chat.project_id)
    if chat.domain != Domain.code:
        raise HTTPException(status_code=400, detail="Файловые инструменты доступны в разделе Код")
    root = _project_root(project)

    permissions = None
    if chat.persona_id:
        persona = await session.get(Persona, chat.persona_id)
        if persona is None or (not persona.is_builtin and persona.owner_id != user.id):
            raise HTTPException(status_code=404, detail="Персона не найдена")
        permissions = persona.allowed_tools or []

    model = body.model or chat.model
    if not model:
        raise HTTPException(status_code=400, detail="Не выбрана модель")
    provider = await provider_client.resolve_provider(session, user.id, model)
    if provider is None:
        raise HTTPException(status_code=400, detail="Модель недоступна: нет активного провайдера.")
    provider_id = provider.id

    session.add(Message(chat_id=chat_id, role="user", content=body.content))
    await session.commit()
    payload = await _build_messages(session, chat)
    assistant = Message(chat_id=chat_id, role="assistant", content="", meta={})
    session.add(assistant)
    await session.commit()
    msg_id = assistant.id

    owner_id = user.id
    project_id = chat.project_id
    job = await jobs.create_job(
        session, owner_id=owner_id, domain="code", kind="project.agent", title=body.content[:80]
    )

    async def worker(h: jobs.JobHandle) -> None:
        prov = await h.session.get(Provider, provider_id)
        key = await provider_client.pick_key(h.session, prov)
        full: list[str] = []
        reasoning_all: list[str] = []
        tools: dict[str, dict] = {}

        async def persist(change: dict | None = None) -> None:
            msg = await h.session.get(Message, msg_id)
            if msg is None:
                return
            msg.content = "".join(full)
            msg.meta = {"reasoning": "".join(reasoning_all), "tools": list(tools.values()), "error": None}
            if change:
                await audit.record(
                    h.session,
                    actor=owner_id,
                    action="project.agent." + change["operation"],
                    target=project_id,
                    meta={"path": change["path"]},
                )
            await h.session.commit()

        await h.step("Агент анализирует задачу", progress=0.1)
        async for event in project_agent.run(prov, key, model, payload, root, permissions):
            if "delta" in event:
                full.append(event["delta"])
            elif "reasoning" in event:
                reasoning_all.append(event["reasoning"])
                await h.reason(event["reasoning"])
            elif "tool" in event:
                tool = event["tool"]
                tools[tool["id"]] = tool
                if tool["status"] != "running":
                    label = _TOOL_LABELS.get(tool["name"], tool["name"])
                    await h.step(f"{label}: {tool.get('path', '')}")
                    await persist(tool.get("change"))
        await persist()
        await h.set_result({"chat_id": chat_id, "message_id": msg_id})
        await h.step("Готово", progress=1.0)

    jobs.launch(maker, job.id, worker)
    return job
