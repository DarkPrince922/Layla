"""Экспорт аудит-лога (спец. §7.7). Только собственные записи оператора."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.audit import AuditLog
from app.models.user import User
from app.services.auth import get_current_user

router = APIRouter(prefix="/audit", tags=["audit"])


async def _rows(session: AsyncSession, user: User, action: str | None, limit: int):
    stmt = select(AuditLog).where(AuditLog.actor == user.id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
    return list(await session.scalars(stmt))


@router.get("")
async def list_audit(
    action: str | None = Query(default=None),
    limit: int = Query(default=200, le=2000),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = await _rows(session, user, action, limit)
    return [
        {
            "id": r.id, "action": r.action, "target": r.target,
            "meta": r.meta, "note": r.note, "ts": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/export.csv", response_class=PlainTextResponse)
async def export_csv(
    action: str | None = Query(default=None),
    limit: int = Query(default=2000, le=10000),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    rows = await _rows(session, user, action, limit)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ts", "action", "target", "note"])
    for r in rows:
        w.writerow([r.created_at.isoformat(), r.action, r.target or "", r.note or ""])
    return PlainTextResponse(
        buf.getvalue(),
        headers={"Content-Disposition": "attachment; filename=layla-audit.csv"},
    )
