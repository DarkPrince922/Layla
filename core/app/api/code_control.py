"""Authenticated user controls for Code plans, recovery and spending limits."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, get_sessionmaker
from app.models.chat import Chat
from app.models.code_control import CodePlan
from app.models.enums import Domain
from app.models.user import User
from app.services import code_context, code_plans, code_state, code_usage
from app.services.auth import get_current_user

router = APIRouter(prefix="/chats/{chat_id}/code-control", tags=["code-control"])


async def bound(chat_id, user, session, maker):
    chat = await session.get(Chat, chat_id)
    if not chat or chat.owner_id != user.id or chat.domain != Domain.code or chat.deleted_at or not chat.project_id:
        raise HTTPException(404, "Code-чат недоступен")
    runtime = code_state.Runtime(maker, user.id, chat_id, chat.project_id)
    try:
        await runtime._authorize(session)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return chat, runtime


@router.get("")
async def controls(chat_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)):
    chat, runtime = await bound(chat_id, user, session, maker)
    rows = list(await session.scalars(select(CodePlan).where(CodePlan.chat_id == chat_id,
        CodePlan.project_id == chat.project_id).order_by(CodePlan.created_at.desc()).limit(10)))
    for row in rows:
        await code_plans.reconcile(session, row)
    await session.commit()
    return {"plans": [code_plans.public(row) for row in rows], "recovery": await code_context.latest(runtime),
            "usage": await code_usage.snapshot(runtime, chat.provider_id, chat.model)}


class Reject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback: str = Field(default="", max_length=8000)


@router.post("/plans/{plan_id}/reject")
async def reject(chat_id: str, plan_id: str, body: Reject, user: User = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)):
    chat, _runtime = await bound(chat_id, user, session, maker)
    await session.scalar(select(Chat).where(Chat.id == chat_id).with_for_update())
    row = await session.scalar(select(CodePlan).where(CodePlan.id == plan_id, CodePlan.chat_id == chat_id,
        CodePlan.owner_id == user.id, CodePlan.project_id == chat.project_id).with_for_update())
    if row is None or row.status != "pending":
        raise HTTPException(409, "План уже обработан или недоступен")
    row.status, row.feedback = "rejected", body.feedback
    await session.commit()
    return code_plans.public(row)


@router.patch("/usage")
async def policy(chat_id: str, body: code_usage.Policy, user: User = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)):
    chat, runtime = await bound(chat_id, user, session, maker)
    try:
        await code_usage.configure(runtime, body, chat.provider_id, chat.model)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return await code_usage.snapshot(runtime, chat.provider_id, chat.model)
