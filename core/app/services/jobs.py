"""Server-owned jobs. Leaving a page never cancels a worker or its saved output."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.chat import Message
from app.models.job import Job

logger = logging.getLogger("layla.jobs")
ACTIVE = ("queued", "running")
_tasks: dict[str, asyncio.Task] = {}


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
        return False
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
                    {**t, "status": "error", "error": error} if t.get("status") == "running" else t
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
