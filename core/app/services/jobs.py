"""Раннер фоновых задач (in-process).

Задача запускается через ``launch`` и выполняется в отдельной корутине со своей
сессией БД, периодически записывая прогресс, шаги и размышление модели в строку
Job. UI опрашивает /api/jobs и показывает это в панели «В работе».

Ограничение: раннер живёт в процессе uvicorn (одна реплика). Незавершённые при
перезапуске контейнера задачи остаются в статусе running — при старте их можно
пометить как прерванные (см. reap_stale).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.job import Job

logger = logging.getLogger("layla.jobs")


async def create_job(
    session: AsyncSession, *, owner_id: str, domain: str, kind: str, title: str
) -> Job:
    job = Job(owner_id=owner_id, domain=domain, kind=kind, title=title, status="queued")
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


class JobHandle:
    """Обёртка для воркера: пишет прогресс/шаги/размышление в строку Job."""

    def __init__(self, session: AsyncSession, job: Job) -> None:
        self.session = session
        self.job = job
        self._reason_buffered = 0

    async def step(self, text: str, *, progress: float | None = None) -> None:
        steps = list(self.job.steps or [])
        steps.append({"text": text, "at": datetime.now(timezone.utc).isoformat()})
        self.job.steps = steps
        if progress is not None:
            self.job.progress = progress
        await self.session.commit()

    async def reason(self, chunk: str, *, flush: bool = False) -> None:
        if not chunk:
            return
        self.job.reasoning = (self.job.reasoning or "") + chunk
        self._reason_buffered += len(chunk)
        # Не коммитим на каждый токен — раз в ~300 символов или по флагу.
        if flush or self._reason_buffered >= 300:
            self._reason_buffered = 0
            await self.session.commit()

    async def set_progress(self, value: float) -> None:
        self.job.progress = max(0.0, min(1.0, value))
        await self.session.commit()

    async def set_result(self, result: dict) -> None:
        self.job.result = result
        await self.session.commit()


def launch(sessionmaker: async_sessionmaker[AsyncSession], job_id: str, worker) -> None:
    """Запустить воркера в фоне. worker: async fn(JobHandle) -> None."""
    asyncio.create_task(_run(sessionmaker, job_id, worker))


async def _run(sessionmaker, job_id: str, worker) -> None:
    async with sessionmaker() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        job.status = "running"
        await session.commit()
        handle = JobHandle(session, job)
        try:
            await worker(handle)
            if job.status == "running":
                job.status = "done"
                job.progress = 1.0
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — фон не должен ронять процесс
            logger.exception("Фоновая задача %s завершилась с ошибкой", job_id)
            job.status = "error"
            job.error = str(exc)[:2000]
            await session.commit()


async def reap_stale(sessionmaker) -> int:
    """Пометить задачи, зависшие в running после перезапуска, как прерванные."""
    async with sessionmaker() as session:
        result = await session.execute(
            update(Job)
            .where(Job.status.in_(["running", "queued"]))
            .values(status="error", error="Прервано перезапуском сервера")
        )
        await session.commit()
        return result.rowcount or 0
