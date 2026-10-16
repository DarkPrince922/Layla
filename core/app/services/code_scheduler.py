"""Leased, durable Code schedules. The normal chat engine owns each execution."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select, update
from starlette.concurrency import run_in_threadpool

from app.models.code_schedule import CodeSchedule
from app.models.code_workspace import CodeWorkspace
from app.models.user import User

logger = logging.getLogger(__name__)
MODES = Literal["auto", "confirm", "plan", "review"]


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def cron_fields(expression):
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("Cron: нужны пять полей: минута час день месяц день-недели")
    result = []
    for field, (low, high) in zip(fields, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)], strict=True):
        values = set()
        for part in field.split(","):
            match = re.fullmatch(r"(\*|\d+(?:-\d+)?)(?:/(\d+))?", part)
            if not match:
                raise ValueError("Cron: поддерживаются числа, *, списки, диапазоны и шаги")
            token, step = match[1], int(match[2] or 1)
            if not 1 <= step <= 1000 or (match[2] and token != "*" and "-" not in token):
                raise ValueError("Cron: недопустимый шаг")
            start, end = (low, high) if token == "*" else map(int, token.split("-")) if "-" in token else (int(token), int(token))
            maximum = 7 if high == 6 else high
            if not low <= start <= end <= maximum:
                raise ValueError("Cron: значение вне диапазона")
            values.update(0 if high == 6 and n == 7 else n for n in range(start, end + 1, step))
        result.append(values)
    return result


def next_run(cron, timezone, after):
    minute, hour, dom, month, dow = cron_fields(cron)
    zone = ZoneInfo(timezone)
    current = aware(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
    # Search UTC instants; nonexistent local DST times are skipped naturally.
    for _ in range(366 * 24 * 60):
        local = current.astimezone(zone)
        weekday = (local.weekday() + 1) % 7
        day_match = ((local.day in dom and weekday in dow) if len(dom) == 31 or len(dow) == 7
                     else local.day in dom or weekday in dow)
        if (local.minute in minute and local.hour in hour and local.month in month and day_match
                and local.fold == 0):  # repeated fixed wall-clock minute fires once
            return current
        current += timedelta(minutes=1)
    raise ValueError("Cron не срабатывает в ближайшие 366 дней")


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=8000)
    cron: str | None = Field(default=None, max_length=120)
    at: datetime | None = None
    timezone: str = Field(default="UTC", max_length=100)
    recurring: bool = False
    mode: MODES = "confirm"

    @model_validator(mode="after")
    def valid(self):
        if not self.prompt.strip() or (self.cron is None) == (self.at is None):
            raise ValueError("Укажите непустую задачу и ровно одно из cron или at")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Укажите часовой пояс IANA, например Europe/Moscow") from exc
        if self.cron:
            cron_fields(self.cron)
        if self.at and (self.at.tzinfo is None or self.recurring):
            raise ValueError("at требует часовой пояс и используется только для разовой задачи")
        return self


class Identify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=36)


class Toggle(Identify):
    enabled: bool


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


SCHEMAS = {"cron_create": Create, "cron_list": Empty, "cron_delete": Identify, "cron_update": Toggle}
TOOLS = [{"type": "function", "function": {"name": name,
    "description": description, "parameters": SCHEMAS[name].model_json_schema()}}
    for name, description in {
        "cron_create": "Schedule a future prompt in this Code chat. Use a five-field cron and explicit IANA timezone or an ISO at timestamp with offset. recurring=false fires once. Execution keeps current persona and confirmation rules. State is durable across restarts. Never schedule a broader mode than the current request.",
        "cron_list": "List durable schedules in this Code chat, including next time, errors and last job.",
        "cron_delete": "Delete a future schedule by ID. Already admitted jobs are stopped through the usual job controls.",
        "cron_update": "Pause or resume a schedule. Resuming a recurring schedule computes its next future occurrence.",
    }.items()]


def public(row):
    result = {key: getattr(row, key) for key in ("id", "prompt", "cron", "timezone", "recurring", "mode", "workspace_id",
            "enabled", "next_run", "last_run", "last_job_id", "last_error")}
    return {key: aware(value).isoformat() if isinstance(value, datetime) else value for key, value in result.items()}


class Runtime:
    def __init__(self, state, mode="confirm"):
        self.state, self.mode = state, mode

    async def call(self, name, arguments):
        args = SCHEMAS[name].model_validate(arguments)
        async with self.state.maker() as session:
            await self.state._authorize(session)
            # Serialize catalogue mutations on the chat row as well as individual leases.
            from app.models.chat import Chat
            await session.scalar(select(Chat).where(Chat.id == self.state.chat_id).with_for_update())
            workspace_id = await session.scalar(select(CodeWorkspace.id).where(
                CodeWorkspace.chat_id == self.state.chat_id, CodeWorkspace.owner_id == self.state.owner_id,
                CodeWorkspace.project_id == self.state.project_id, CodeWorkspace.active_slot == 1))
            scope = [CodeSchedule.owner_id == self.state.owner_id, CodeSchedule.chat_id == self.state.chat_id,
                     CodeSchedule.project_id == self.state.project_id]
            if name == "cron_list":
                return {"schedules": [public(r) for r in await session.scalars(
                    select(CodeSchedule).where(*scope).order_by(CodeSchedule.created_at))]}
            if self.mode in ("plan", "review"):
                raise ValueError("В этом режиме расписания доступны только для чтения")
            now = datetime.now(UTC)
            if name == "cron_create":
                if self.mode in ("plan", "review") or (self.mode == "confirm" and args.mode == "auto"):
                    raise ValueError("Расписание не может расширять текущий режим выполнения")
                count = await session.scalar(select(func.count()).select_from(CodeSchedule).where(*scope))
                if count >= 50:
                    raise ValueError("Не больше 50 расписаний на Code-чат")
                due = await run_in_threadpool(next_run, args.cron, args.timezone, now) if args.cron else aware(args.at)
                if due <= now:
                    raise ValueError("Время запуска должно быть в будущем")
                row = CodeSchedule(owner_id=self.state.owner_id, chat_id=self.state.chat_id,
                    project_id=self.state.project_id, workspace_id=workspace_id, prompt=args.prompt, cron=args.cron,
                    timezone=args.timezone, recurring=args.recurring, mode=args.mode, next_run=due)
                session.add(row)
            else:
                row = await session.scalar(select(CodeSchedule).where(*scope, CodeSchedule.id == args.id).with_for_update())
                if row is None:
                    raise ValueError("Расписание не найдено")
                if name == "cron_update" and args.enabled and self.mode == "confirm" and row.mode == "auto":
                    raise ValueError("Расписание не может расширять текущий режим выполнения")
                if name == "cron_update" and args.enabled and row.workspace_id != workspace_id:
                    raise ValueError("Вернитесь в рабочую копию расписания или создайте новое")
                if name == "cron_delete":
                    await session.delete(row)
                    await session.commit()
                    return {"deleted": args.id}
                row.enabled, row.lease_token, row.lease_until = args.enabled, None, None
                if args.enabled:
                    if row.recurring:
                        row.next_run = await run_in_threadpool(next_run, row.cron, row.timezone, now)
                    elif aware(row.next_run) <= now:
                        raise ValueError("Разовый запуск уже прошёл: создайте новое расписание")
                row.last_error = None
            await session.commit()
            return {"schedule": public(row)}


async def tick(maker, *, now=None, dispatch=None):
    """Claim and admit up to 20 occurrences; a fixed request ID closes crash windows."""
    from app.api.chats import run_chat
    from app.models.chat import Chat
    from app.schemas.chat import SendMessageRequest
    from app.services.code_state import Runtime as State

    now = now or datetime.now(UTC)
    dispatch = dispatch or run_chat
    async with maker() as session:
        ids = list(await session.scalars(select(CodeSchedule.id).where(
            CodeSchedule.enabled.is_(True), CodeSchedule.next_run <= now,
            or_(CodeSchedule.lease_until.is_(None), CodeSchedule.lease_until <= now),
        ).order_by(CodeSchedule.next_run).limit(20)))
    for schedule_id in ids:
        token = str(uuid4())
        async with maker() as session:
            claimed = await session.execute(update(CodeSchedule).where(
                CodeSchedule.id == schedule_id, CodeSchedule.enabled.is_(True), CodeSchedule.next_run <= now,
                or_(CodeSchedule.lease_until.is_(None), CodeSchedule.lease_until <= now),
            ).values(lease_token=token, lease_until=now + timedelta(seconds=60)))
            await session.commit()
            if not claimed.rowcount:
                continue
        job, error, disable = None, None, False
        try:
            async with maker() as session, asyncio.timeout(45):
                row = await session.scalar(select(CodeSchedule).where(CodeSchedule.id == schedule_id,
                    CodeSchedule.lease_token == token, CodeSchedule.enabled.is_(True)))
                if row is None:
                    continue
                # Workspace switches and chat admission use this same lock. Keep
                # chat -> schedule ordering consistent with catalogue mutations.
                await session.scalar(select(Chat).where(Chat.id == row.chat_id).with_for_update())
                row = await session.scalar(select(CodeSchedule).where(CodeSchedule.id == schedule_id,
                    CodeSchedule.lease_token == token, CodeSchedule.enabled.is_(True))
                    .with_for_update().execution_options(populate_existing=True))
                if row is None:
                    continue
                await State(maker, row.owner_id, row.chat_id, row.project_id)._authorize(session)
                workspace_id = await session.scalar(select(CodeWorkspace.id).where(
                    CodeWorkspace.chat_id == row.chat_id, CodeWorkspace.owner_id == row.owner_id,
                    CodeWorkspace.project_id == row.project_id, CodeWorkspace.active_slot == 1))
                if workspace_id != row.workspace_id:
                    raise ValueError("Рабочая копия чата изменилась; расписание приостановлено")
                user = await session.get(User, row.owner_id)
                if user is None or not user.is_active:
                    raise ValueError("Владелец расписания недоступен")
                chat = await session.get(Chat, row.chat_id)
                if chat.model is None:
                    raise ValueError("В Code-чате не выбрана модель")
                occurrence = aware(row.next_run)
                job = await dispatch(row.chat_id, SendMessageRequest(content=row.prompt, mode=row.mode,
                    request_id=f"cron:{row.id}:{int(occurrence.timestamp())}"), user, session, maker)
        except HTTPException as exc:
            error = "Чат занят; запуск будет повторён" if exc.status_code == 409 else "Запуск Code недоступен: проверьте модель и права"
            disable = exc.status_code == 404
        except ValueError as exc:
            error, disable = str(exc)[:500], True
        except Exception:  # noqa: BLE001 — keep other schedules alive, never persist provider secrets
            error = "Не удалось запустить задачу; повтор после освобождения lease"
            logger.warning("Code schedule admission failed: %s", schedule_id)
        async with maker() as session:
            row = await session.scalar(select(CodeSchedule).where(CodeSchedule.id == schedule_id,
                CodeSchedule.lease_token == token).with_for_update())
            if row is None:
                continue
            row.last_error, row.lease_token = error, None
            row.lease_until = now + timedelta(seconds=30) if error else None
            if disable:
                row.enabled = False
            if job is not None:
                row.last_run, row.last_job_id = now, job.id
                if row.recurring:
                    try:
                        row.next_run = await run_in_threadpool(next_run, row.cron, row.timezone, now)
                    except ValueError:
                        row.enabled = False
                        row.last_error = "Следующий запуск вне горизонта 366 дней; измените расписание"
                else:
                    row.enabled = False
            await session.commit()


async def run_forever(maker):
    while True:
        try:
            await tick(maker)
        except Exception:  # noqa: BLE001 — transient DB errors must not stop future ticks
            logger.warning("Code scheduler tick failed")
        await asyncio.sleep(10)
