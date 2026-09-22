"""Фоновые задачи: список и детали для панели «В работе»."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.job import Job
from app.models.user import User
from app.schemas.job import JobOut
from app.services.auth import get_current_user

router = APIRouter(prefix="/jobs", tags=["jobs"])

_ACTIVE = ("queued", "running")


@router.get("", response_model=list[JobOut])
async def list_jobs(
    active: bool = Query(default=False),
    limit: int = Query(default=30, le=100),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Job]:
    query = select(Job).where(Job.owner_id == user.id)
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
