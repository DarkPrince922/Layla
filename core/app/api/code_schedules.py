"""Code-only schedules, scoped to the authenticated owner/chat/project."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, get_sessionmaker
from app.models.chat import Chat
from app.models.enums import Domain
from app.models.user import User
from app.services import code_scheduler, code_state
from app.services.auth import get_current_user

router = APIRouter(prefix="/chats/{chat_id}/schedules", tags=["code-schedules"])


async def invoke(chat_id, name, arguments, user, session, maker):
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.owner_id != user.id or chat.domain != Domain.code or not chat.project_id or chat.deleted_at:
        raise HTTPException(404, "Code-чат не найден")
    state = code_state.Runtime(maker, user.id, chat.id, chat.project_id)
    try:
        return await code_scheduler.Runtime(state, mode="auto").call(name, arguments)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("")
async def list_schedules(chat_id: str, user: User = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)):
    return await invoke(chat_id, "cron_list", {}, user, session, maker)


@router.post("", status_code=201)
async def create_schedule(chat_id: str, body: code_scheduler.Create, user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)):
    return await invoke(chat_id, "cron_create", body.model_dump(), user, session, maker)


@router.patch("/{schedule_id}")
async def toggle_schedule(chat_id: str, schedule_id: str, body: dict,
                          user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                          maker=Depends(get_sessionmaker)):
    return await invoke(chat_id, "cron_update", {**body, "id": schedule_id}, user, session, maker)


@router.delete("/{schedule_id}")
async def delete_schedule(chat_id: str, schedule_id: str, user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)):
    return await invoke(chat_id, "cron_delete", {"id": schedule_id}, user, session, maker)
