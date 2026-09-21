"""Чаты и стриминг ответов (спец. §5.2).

Ответ стримится из LiteLLM по SSE. Системный промпт берётся из выбранной
персоны. Оба сообщения (пользователя и ассистента) сохраняются в истории.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import get_session, get_sessionmaker
from app.models.chat import Chat, Message
from app.models.persona import Persona
from app.models.user import User
from app.schemas.chat import ChatCreate, ChatDetail, ChatOut, MessageOut, SendMessageRequest
from app.services import provider_client
from app.services.auth import get_current_user

router = APIRouter(prefix="/chats", tags=["chats"])


async def _owned_chat(session: AsyncSession, user: User, chat_id: str) -> Chat:
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден")
    return chat


@router.get("", response_model=list[ChatOut])
async def list_chats(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Chat]:
    rows = await session.scalars(
        select(Chat).where(Chat.owner_id == user.id).order_by(Chat.created_at.desc())
    )
    return list(rows)


@router.post("", response_model=ChatOut, status_code=201)
async def create_chat(
    body: ChatCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Chat:
    chat = Chat(
        owner_id=user.id,
        domain=body.domain,
        title=body.title or "Новый чат",
        persona_id=body.persona_id,
        model=body.model,
        workspace_id=body.workspace_id,
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
            out.append({"role": m.role, "content": m.content})
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

    async def event_stream() -> AsyncIterator[bytes]:
        full = []
        try:
            async for delta in provider_client.stream_chat(provider, key, model, payload):
                full.append(delta)
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n".encode()
        except Exception as exc:  # сеть/ошибка провайдера
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n".encode()
        text = "".join(full)
        # Сохранить ответ ассистента (даже частичный).
        async with maker() as session:
            msg = Message(chat_id=chat_id, role="assistant", content=text)
            session.add(msg)
            await session.commit()
            msg_id = msg.id
        yield f"data: {json.dumps({'done': True, 'message_id': msg_id})}\n\n".encode()

    return StreamingResponse(event_stream(), media_type="text/event-stream")
