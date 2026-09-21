"""Настройка Telegram-бота и тест-отправка (спец. §5.8)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.telegram import TelegramConfig
from app.models.user import User
from app.schemas.telegram import TelegramConfigIn, TelegramConfigOut, TelegramTestRequest
from app.security import crypto
from app.services import audit, telegram
from app.services.auth import get_current_user

router = APIRouter(prefix="/integrations/telegram", tags=["integrations"])


async def _config(session: AsyncSession, user: User) -> TelegramConfig | None:
    return await session.scalar(
        select(TelegramConfig).where(TelegramConfig.owner_id == user.id)
    )


@router.get("", response_model=TelegramConfigOut)
async def get_config(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramConfigOut:
    cfg = await _config(session, user)
    if cfg is None:
        return TelegramConfigOut(configured=False)
    masked = None
    try:
        masked = crypto.mask(crypto.decrypt(cfg.bot_token_ref))
    except ValueError:
        masked = "••••"
    return TelegramConfigOut(
        configured=True,
        enabled=cfg.enabled,
        default_chat_id=cfg.default_chat_id,
        token_masked=masked,
    )


@router.put("", response_model=TelegramConfigOut)
async def set_config(
    body: TelegramConfigIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramConfigOut:
    cfg = await _config(session, user)
    if cfg is None:
        cfg = TelegramConfig(owner_id=user.id, bot_token_ref=crypto.encrypt(body.bot_token))
        session.add(cfg)
    else:
        cfg.bot_token_ref = crypto.encrypt(body.bot_token)
    cfg.default_chat_id = body.default_chat_id
    cfg.enabled = body.enabled
    await audit.record(session, actor=user.id, action="telegram.configure")
    await session.commit()
    return await get_config(user=user, session=session)


@router.delete("", status_code=204)
async def delete_config(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    cfg = await _config(session, user)
    if cfg is not None:
        await session.delete(cfg)
        await session.commit()


@router.post("/test")
async def test_send(
    body: TelegramTestRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    cfg = await _config(session, user)
    if cfg is None:
        raise HTTPException(status_code=400, detail="Telegram не настроен")
    chat_id = body.chat_id or cfg.default_chat_id
    if not chat_id:
        raise HTTPException(status_code=400, detail="Не указан chat_id")
    token = crypto.decrypt(cfg.bot_token_ref)
    try:
        await telegram.send_message(token, chat_id, body.text)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}
