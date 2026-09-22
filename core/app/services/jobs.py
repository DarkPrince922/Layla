"""Server-owned jobs. Leaving a page never cancels a worker or its saved output."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.chat import Message
from app.models.job import Job

logger = logging.getLogger("layla.jobs")
ACTIVE = ("queued", "running")
_tasks: dict[str, asyncio.Task] = {}
# Ожидающие решения пользователя (режим «С подтверждением»): job_id -> (approval_id, future).
_decisions: dict[str, tuple[str, asyncio.Future]] = {}
DECISIONS = ("approve", "reject", "approve_all")
APPROVAL_TIMEOUT = 20 * 60
# Задача только что создана, но воркер ещё не запущен — не считать её «потерянной».
ORPHAN_GRACE = timedelta(seconds=20)


def public_error(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "Превышено время ожидания. Уже выполненные изменения сохранены."
    if isinstance(exc, RuntimeError):
        return str(exc)[:1500]
    return "Не удалось завершить задачу. Выполненные изменения сохранены; попробуйте продолжить чат."


async def create_job(
    session: AsyncSession, *, owner_id: str, domain: str, kind: str, title: str
) -> Job:
    job = Job(owner_id=owner_id, domain=domain, kind=kind, title=title, status="queued",
              created_at=datetime.now(UTC))
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


class JobHandle:
    def __init__(self, session: AsyncSession, job: Job) -> None:
        self.session = session
        self.job = job
        self._reason_buffered = 0

    async def step(self, text: str, *, progress: float | None = None) -> None:
        steps = list(self.job.steps or [])
        steps.append({"text": text, "at": datetime.now(UTC).isoformat()})
        self.job.steps = steps[-200:]
        if progress is not None:
            self.job.progress = progress
        await self.session.commit()

    async def reason(self, chunk: str, *, flush: bool = False) -> None:
        self.job.reasoning = ((self.job.reasoning or "") + chunk)[-200_000:]
        self._reason_buffered += len(chunk)
        if flush or self._reason_buffered >= 300:
            self._reason_buffered = 0
            await self.session.commit()

    async def set_progress(self, value: float) -> None:
        self.job.progress = max(0.0, min(1.0, value))
        await self.session.commit()

    async def set_result(self, result: dict) -> None:
        self.job.result = {**(self.job.result or {}), **result}
        await self.session.commit()


def launch(sessionmaker: async_sessionmaker[AsyncSession], job_id: str, worker) -> None:
    task = asyncio.create_task(_run(sessionmaker, job_id, worker), name=f"layla-job-{job_id}")
    _tasks[job_id] = task
    task.add_done_callback(lambda done: _tasks.pop(job_id, None))


async def cancel(job_id: str, maker) -> bool:
    task = _tasks.get(job_id)
    if task is None:
        # Воркера нет — значит, он уже мёртв (один процесс Core). «Остановить»
        # должно освобождать чат, а не отвечать бесконечным «уже завершается».
        async with maker() as session:
            job = await session.get(Job, job_id)
            unfinished = job is not None and job.status in ACTIVE
        if unfinished:
            await _finish(maker, job_id, "cancelled", "Задача остановлена. Выполненные изменения сохранены.")
        return True
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    # A task cancelled before its first instruction cannot run its finally block.
    async with maker() as session:
        job = await session.get(Job, job_id)
        unfinished = job is not None and job.status in ACTIVE
    if unfinished:
        await _finish(maker, job_id, "cancelled", "Задача остановлена. Выполненные изменения сохранены.")
    return True


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def is_orphaned(job: Job) -> bool:
    """Активна в БД, но воркера нет. Core — один процесс, поэтому _tasks полон.

    Так бывает, если воркер умер, не успев записать итог (сбой БД при завершении
    и т. п.). Раньше такой чат навсегда отвечал «уже выполняется задача».
    """
    if job.status not in ACTIVE or job.id in _tasks:
        return False
    last = _aware(job.updated_at) or _aware(job.created_at)
    return last is None or datetime.now(UTC) - last > ORPHAN_GRACE


async def release_orphan(maker, job_id: str) -> None:
    await _finish(maker, job_id, "error", "Предыдущий ответ прервался. История и файлы сохранены.")


async def wait_decision(job_id: str, approval_id: str, timeout: float = APPROVAL_TIMEOUT) -> str:
    """Ждать решения пользователя по изменению. Без ответа — изменение отклоняется."""
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _decisions[job_id] = (approval_id, future)
    try:
        return await asyncio.wait_for(future, timeout)
    except TimeoutError:
        return "reject"
    finally:
        if _decisions.get(job_id, (None, None))[1] is future:
            _decisions.pop(job_id, None)


def decide(job_id: str, approval_id: str, decision: str) -> bool:
    entry = _decisions.get(job_id)
    if entry is None or entry[0] != approval_id or entry[1].done() or decision not in DECISIONS:
        return False
    entry[1].set_result(decision)
    return True


async def shutdown() -> None:
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _heartbeat(maker, job_id: str) -> None:
    while True:
        await asyncio.sleep(5)
        try:
            async with maker() as session:
                await session.execute(update(Job).where(Job.id == job_id, Job.status.in_(ACTIVE))
                                      .values(updated_at=datetime.now(UTC)))
                await session.commit()
        except Exception:  # noqa: BLE001 — transient heartbeat failures do not cancel work
            logger.warning("Не удалось обновить heartbeat задачи %s", job_id)


async def _finish(maker, job_id: str, status: str, error: str | None = None) -> None:
    # Separate session: a failed worker transaction must not swallow its failure state.
    async with maker() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        job.status = status
        job.error = error
        if status == "done":
            job.progress = 1.0
        message_id = (job.result or {}).get("message_id")
        if error and message_id:
            msg = await session.get(Message, message_id)
            if msg and msg.chat_id == job.chat_id:
                meta = dict(msg.meta or {})
                meta["error"] = error
                meta["tools"] = [
                    {**t, "status": "error", "error": error} if t.get("status") in ("running", "pending") else t
                    for t in meta.get("tools", [])
                ]
                msg.meta = meta
        await session.commit()


async def _run(sessionmaker, job_id: str, worker) -> None:
    heartbeat = None
    status, error = "done", None
    try:
        async with sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status != "queued":
                return
            job.status = "running"
            await session.commit()
            heartbeat = asyncio.create_task(_heartbeat(sessionmaker, job_id))
            async with asyncio.timeout(3600):
                await worker(JobHandle(session, job))
    except asyncio.CancelledError:
        status, error = "cancelled", "Задача остановлена. Выполненные изменения сохранены."
    except Exception as exc:
        logger.exception("Фоновая задача %s завершилась с ошибкой", job_id)
        status, error = "error", public_error(exc)
    finally:
        if heartbeat:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
    await _finish(sessionmaker, job_id, status, error)


async def reap_stale(sessionmaker) -> int:
    async with sessionmaker() as session:
        ids = list(await session.scalars(select(Job.id).where(Job.status.in_(ACTIVE))))
    for job_id in ids:
        await _finish(sessionmaker, job_id, "error", "Прервано перезапуском сервера. История и файлы сохранены.")
    return len(ids)
