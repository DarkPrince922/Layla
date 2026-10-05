"""Single-process long polling and a durable notification dispatcher.

Core already owns workers in one process. Running another bot poller for the
same token would conflict with Telegram's getUpdates API.
"""
from __future__ import annotations
import asyncio
import time
from contextlib import suppress

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.agent import AgentRun, AgentStep
from app.models.chat import Message
from app.models.job import Job
from app.models.telegram import TelegramConfig
from app.models.telegram_bot import TelegramBotState, TelegramNotification
from app.models.user import User
from app.security import crypto
from app.services import telegram


async def queue(session, cfg, event_key, text, job_id=None):
    if not cfg.default_chat_id:
        return
    exists = await session.scalar(select(TelegramNotification.id).where(
        TelegramNotification.config_id == cfg.id, TelegramNotification.event_key == event_key))
    if exists:
        return
    try:
        async with session.begin_nested():
            session.add(TelegramNotification(config_id=cfg.id, event_key=event_key, job_id=job_id,
                chat_id=cfg.default_chat_id, text=text[:25000], created_at=time.time()))
            await session.flush()
    except IntegrityError:
        pass  # competing completion callbacks must not roll back the job's final state


async def enqueue_finished(session, job):
    cfg = await session.scalar(select(TelegramConfig).where(TelegramConfig.owner_id == job.owner_id,
                                                          TelegramConfig.enabled.is_(True)))
    if not cfg or not cfg.default_chat_id:
        return
    bot = await session.scalar(select(TelegramBotState).where(TelegramBotState.config_id == cfg.id))
    field = {"done": "notify_done", "error": "notify_error", "cancelled": "notify_cancelled"}.get(job.status)
    if not field or (bot and not getattr(bot, field)):
        return
    status = {"done": "✅ Работа завершена", "error": "❌ Работа завершилась ошибкой", "cancelled": "⏹ Работа остановлена"}[job.status]
    text = f"{status}\n{job.domain.upper()} · {job.title}\nЗадача: {job.id}\n"
    result = job.result or {}
    summary = result.get("content") or result.get("text") or result.get("draft") or ""
    if result.get("message_id"):
        message = await session.get(Message, result["message_id"])
        if message and message.chat_id == job.chat_id:
            summary = message.content
    if job.error:
        text += str(job.error)[:1500] + "\n"
    if summary:
        text += str(summary)[:2200] + "\n"
    text += "Полный результат: /tasks → задача → Полный результат."
    if bot and bot.public_url:
        text += "\n" + bot.public_url
    await queue(session, cfg, "finish:" + job.id, text, job.id)


async def scan_approvals(maker):
    async with maker() as session:
        configs = list(await session.scalars(select(TelegramConfig).where(TelegramConfig.enabled.is_(True))))
        for cfg in configs:
            bot = await session.scalar(select(TelegramBotState).where(TelegramBotState.config_id == cfg.id))
            if not bot or not bot.notify_approval or not cfg.default_chat_id:
                continue
            jobs = list(await session.scalars(select(Job).where(Job.owner_id == cfg.owner_id, Job.status.in_(("queued", "running")))))
            for job in jobs:
                approval = (job.result or {}).get("approval")
                if approval and approval.get("id"):
                    text = f"⚠️ Агент ждёт подтверждения\n{job.domain.upper()} · {job.title}\n{approval.get('name', '')}\n{approval.get('command') or approval.get('path') or ''}\nОткройте задачу в /tasks, чтобы применить или отклонить."
                    await queue(session, cfg, f"approval:{job.id}:{approval['id']}", text, job.id)
            steps = list(await session.scalars(select(AgentStep).join(AgentRun, AgentRun.id == AgentStep.run_id)
                .where(AgentRun.owner_id == cfg.owner_id, AgentStep.status == "awaiting_approval")))
            for step in steps:
                text = f"⚠️ Пентест ждёт подтверждения\n{step.summary or ''}\n{step.command or ''}\nВыберите соответствующий запуск и действие в /workers."
                await queue(session, cfg, "step:" + step.id, text)
        await session.commit()


async def deliver(maker, limit=20):
    async with maker() as session:
        ids = list(await session.scalars(select(TelegramNotification.id).where(
            TelegramNotification.status == "queued", TelegramNotification.next_attempt <= time.time())
            .order_by(TelegramNotification.created_at).limit(limit)))
    for id in ids:
        async with maker() as session:
            notification = await session.get(TelegramNotification, id)
            cfg = await session.get(TelegramConfig, notification.config_id)
            user = await session.get(User, cfg.owner_id) if cfg else None
            if not cfg or not cfg.enabled or not user or not user.is_active:
                continue
            bot = await session.scalar(select(TelegramBotState).where(TelegramBotState.config_id == cfg.id))
            if cfg.default_chat_id != notification.chat_id:
                notification.status = "cancelled"
                await session.commit()
                continue
            token = crypto.decrypt(cfg.bot_token_ref)
        # No DB transaction stays open during network I/O or callback-capability writes.
        parts = telegram.chunks(notification.text)
        try:
            for index in range(notification.sent_chunks, len(parts)):
                body = {"chat_id": notification.chat_id, "text": parts[index]}
                if index == len(parts) - 1 and bot and bot.controller_chat_id == notification.chat_id:
                    from app.services.telegram_bot import Panel
                    panel = Panel(maker, cfg, bot)
                    if notification.job_id:
                        rows = [[panel.button("Открыть задачу", "job", id=notification.job_id),
                                 panel.button("Полный результат", "job_result", id=notification.job_id)]]
                    elif notification.event_key.startswith("step:"):
                        rows = [[panel.button("Проверить действие", "step", id=notification.event_key[5:])]]
                    else:
                        rows = [[panel.button("Меню", "menu")]]
                    await panel.save()
                    body["reply_markup"] = {"inline_keyboard": rows}
                await telegram.call(token, "sendMessage", body)
                async with maker() as session:
                    current = await session.get(TelegramNotification, id)
                    current.sent_chunks = index + 1
                    await session.commit()
            async with maker() as session:
                current = await session.get(TelegramNotification, id)
                current.status = "sent"
                current.last_error = None
                await session.commit()
        except RuntimeError as exc:
            async with maker() as session:
                current = await session.get(TelegramNotification, id)
                current.attempts += 1
                current.last_error = str(exc)[:500]
                current.next_attempt = time.time() + max(getattr(exc, "retry_after", 15), min(3600, 5 * 2 ** min(current.attempts, 9)))
                current_bot = await session.get(TelegramBotState, bot.id) if bot else None
                if current_bot:
                    current_bot.last_error = str(exc)[:500]
                await session.commit()


async def poll_once(maker, config_id):
    async with maker() as session:
        cfg = await session.get(TelegramConfig, config_id)
        bot = await session.scalar(select(TelegramBotState).where(TelegramBotState.config_id == config_id))
        if not cfg or not cfg.enabled or not bot or not bot.control_enabled:
            return
        token, offset = crypto.decrypt(cfg.bot_token_ref), bot.offset
    try:
        updates = await telegram.call(token, "getUpdates", {"offset": offset, "timeout": 10, "limit": 20,
            "allowed_updates": ["message", "callback_query"]}, timeout=15)
        from app.services.telegram_bot import handle_update
        failed = False
        for update in updates or []:
            async with maker() as session:
                current = await session.get(TelegramBotState, bot.id)
                current_cfg = await session.get(TelegramConfig, config_id)
                if (not current or not current_cfg or not current_cfg.enabled or not current.control_enabled
                        or current.token_hash != bot.token_hash or current_cfg.bot_token_ref != cfg.bot_token_ref):
                    return
                if current.offset > update["update_id"]:
                    continue
                # Claim BEFORE side effects: restarts must never silently launch the same task twice.
                current.offset = update["update_id"] + 1
                await session.commit()
            try:
                await handle_update(maker, config_id, update)
            except Exception:
                failed = True
                async with maker() as session:
                    current = await session.get(TelegramBotState, bot.id)
                    if current:
                        current.last_error = "Не удалось обработать сообщение. Отправьте команду ещё раз."
                        await session.commit()
        async with maker() as session:
            current = await session.get(TelegramBotState, bot.id)
            if current and not failed:
                current.last_error = None
                await session.commit()
    except RuntimeError as exc:
        async with maker() as session:
            current = await session.get(TelegramBotState, bot.id)
            if current:
                current.last_error = str(exc)[:500]
                await session.commit()
        await asyncio.sleep(max(5, min(60, getattr(exc, "retry_after", 5))))


async def run_forever(maker):
    pollers = {}
    async def poller(id):
        while True:
            try:
                await poll_once(maker, id)
            except Exception:
                await asyncio.sleep(5)
            await asyncio.sleep(1)
    try:
        while True:
            try:
                async with maker() as session:
                    ids = set(await session.scalars(select(TelegramConfig.id).join(TelegramBotState,
                        TelegramBotState.config_id == TelegramConfig.id).where(TelegramConfig.enabled.is_(True),
                            TelegramBotState.control_enabled.is_(True))))
                for id in ids - pollers.keys():
                    pollers[id] = asyncio.create_task(poller(id), name="layla-telegram-" + id)
                for id in list(pollers.keys() - ids):
                    pollers.pop(id).cancel()
                await scan_approvals(maker)
                await deliver(maker)
            except Exception:
                # Integration failures never stop agents, server startup or the bot supervisor.
                pass
            await asyncio.sleep(3)
    finally:
        for task in pollers.values():
            task.cancel()
        await asyncio.gather(*pollers.values(), return_exceptions=True)
