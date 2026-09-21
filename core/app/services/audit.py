"""Audit logging helper (spec §7.7)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def record(
    session: AsyncSession,
    *,
    actor: str | None,
    action: str,
    target: str | None = None,
    meta: dict | None = None,
    note: str | None = None,
) -> AuditLog:
    entry = AuditLog(actor=actor, action=action, target=target, meta=meta or {}, note=note)
    session.add(entry)
    await session.flush()
    return entry
