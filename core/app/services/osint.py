"""Persist source-attributed observations with idempotent deduplication."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.osint import OsintArtifact
from app.schemas.intelligence import IntelArtifact


async def save_artifact(
    session: AsyncSession, case_id: str, provider: str, target: str, artifact: IntelArtifact
) -> tuple[OsintArtifact, bool]:
    payload = {"provider": provider, "target": target, **artifact.model_dump()}
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    query = select(OsintArtifact).where(
        OsintArtifact.case_id == case_id,
        OsintArtifact.fingerprint == fingerprint,
    )
    existing = await session.scalar(query)
    if existing:
        return existing, False
    row = OsintArtifact(case_id=case_id, fingerprint=fingerprint, **payload)
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        # A simultaneous result import may have inserted the same observation.
        existing = await session.scalar(query)
        if existing is None:
            raise
        return existing, False
    return row, True
