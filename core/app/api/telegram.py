"""Настройка Telegram-бота и тест-отправка (спец. §5.8)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.telegram import TelegramConfig
from app.models.telegram_bot import TelegramBotState, TelegramNotification
from app.models.user import User
from app.schemas.telegram import TelegramConfigIn, TelegramConfigOut, TelegramTestRequest
from app.security import crypto
from app.services import audit, telegram
from app.services.auth import get_current_user

router = APIRouter(prefix="/integrations/telegram", tags=["integrations"])


async def _state(session, cfg):
    state = await session.scalar(select(TelegramBotState).where(TelegramBotState.config_id == cfg.id))
    if state is None:
        state = TelegramBotState(config_id=cfg.id)
        session.add(state)
        await session.flush()
    return state


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
    bot = await _state(session, cfg)
    return TelegramConfigOut(
        configured=True,
        enabled=cfg.enabled,
        default_chat_id=cfg.default_chat_id,
        token_masked=masked,
        **{name: getattr(bot, name) for name in ("control_enabled", "bot_username", "notify_done",
            "notify_error", "notify_cancelled", "notify_approval", "public_url", "last_error")},
        connected=bool(bot.controller_user_id and bot.controller_chat_id),
    )


@router.put("", response_model=TelegramConfigOut)
async def set_config(
    body: TelegramConfigIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramConfigOut:
    cfg = await _config(session, user)
    if cfg is None:
        if not body.bot_token:
            raise HTTPException(422, "Укажите токен бота от BotFather")
        cfg = TelegramConfig(owner_id=user.id, bot_token_ref=crypto.encrypt(body.bot_token))
        session.add(cfg)
        await session.flush()
    elif body.bot_token:
        changed = crypto.decrypt(cfg.bot_token_ref) != body.bot_token
        cfg.bot_token_ref = crypto.encrypt(body.bot_token)
        if changed:
            bot = await _state(session, cfg)
            bot.controller_user_id = bot.controller_chat_id = bot.bot_username = None
            bot.state = {}
            bot.offset = 0
    bot = await _state(session, cfg)
    import hashlib
    bot.token_hash = hashlib.sha256(crypto.decrypt(cfg.bot_token_ref).encode()).hexdigest()
    for field in ("control_enabled", "notify_done", "notify_error", "notify_cancelled", "notify_approval", "public_url"):
        setattr(bot, field, getattr(body, field))
    cfg.default_chat_id = body.default_chat_id
    cfg.enabled = body.enabled
    try:
        await audit.record(session, actor=user.id, action="telegram.configure")
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Этот Telegram-бот уже подключён к другому аккаунту Лейлы") from None
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


@router.post("/pair")
async def pair_bot(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    import hashlib
    import re
    import secrets
    import time
    cfg = await _config(session, user)
    if not cfg or not cfg.enabled:
        raise HTTPException(400, "Сначала сохраните и включите Telegram-бота")
    token = crypto.decrypt(cfg.bot_token_ref)
    try:
        info = await telegram.call(token, "getMe")
        webhook = await telegram.call(token, "getWebhookInfo")
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from None
    if webhook.get("url"):
        raise HTTPException(409, "У бота настроен webhook. Используйте отдельного бота для Лейлы или отключите прежний webhook.")
    username = info.get("username", "")
    if not info.get("is_bot") or not re.fullmatch(r"[A-Za-z0-9_]{5,64}", username):
        raise HTTPException(502, "Telegram не вернул корректное имя бота")
    bot = await _state(session, cfg)
    code = secrets.token_urlsafe(24)
    bot.state = {**(bot.state or {}), "pair_hash": hashlib.sha256(code.encode()).hexdigest(), "pair_expires": time.time() + 600}
    bot.bot_username = username
    bot.control_enabled = True
    bot.last_error = None
    await session.commit()
    return {"url": f"https://t.me/{username}?start={code}", "expires_in": 600}


@router.post("/disconnect")
async def disconnect_bot(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    from sqlalchemy import delete
    cfg = await _config(session, user)
    if cfg:
        bot = await _state(session, cfg)
        bot.controller_user_id = bot.controller_chat_id = None
        bot.state = {}
        cfg.default_chat_id = None
        await session.execute(delete(TelegramNotification).where(TelegramNotification.config_id == cfg.id))
        await session.commit()
    return {"ok": True}
