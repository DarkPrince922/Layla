"""Фоновые задачи: список и детали для панели «В работе»."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, get_sessionmaker
from app.models.job import Job
from app.models.user import User
from app.schemas.chat import DecisionRequest
from app.schemas.job import JobOut
from app.services import jobs as job_service
from app.services.auth import get_current_user

router = APIRouter(prefix="/jobs", tags=["jobs"])

_ACTIVE = ("queued", "running")


@router.get("", response_model=list[JobOut])
async def list_jobs(
    active: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    chat_id: str | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Job]:
    query = select(Job).where(Job.owner_id == user.id)
    if chat_id:
        query = query.where(Job.chat_id == chat_id)
    if active:
        query = query.where(Job.status.in_(_ACTIVE))
    query = query.order_by(Job.created_at.desc()).limit(limit)
    return list(await session.scalars(query))


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Job:
    job = await session.get(Job, job_id)
    if job is None or job.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return job


@router.delete("/{job_id}", status_code=204)
async def dismiss_job(
    job_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Убрать завершённую задачу из списка. Активные удалять нельзя."""
    job = await session.get(Job, job_id)
    if job is None or job.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if job.status in _ACTIVE:
        raise HTTPException(status_code=409, detail="Нельзя убрать активную задачу")
    await session.delete(job)
    await session.commit()


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str, user: User = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session),
                     maker=Depends(get_sessionmaker)) -> Job:
    job = await get_job(job_id, user, session)
    if job.status in _ACTIVE:
        await session.rollback()
        if not await job_service.cancel(job_id, maker):
            # A worker missing from this process is not silently reported as stopped.
            raise HTTPException(status_code=409, detail="Задача уже завершается. Обновите её состояние.")
        job = await session.get(Job, job_id, populate_existing=True)
    return job


@router.post("/{job_id}/decision", response_model=JobOut)
async def decide(job_id: str, body: DecisionRequest, user: User = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session)) -> Job:
    """Решение по изменению в режиме «С подтверждением»: применить / отклонить / применять всё."""
    job = await get_job(job_id, user, session)
    if not job_service.decide(job_id, body.approval_id, body.decision):
        raise HTTPException(status_code=409, detail="Это изменение уже не ждёт решения. Обновите чат.")
    return job
