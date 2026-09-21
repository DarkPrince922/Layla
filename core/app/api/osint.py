"""OSINT cases, manual observations, passive MCP queries and their timeline."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.enums import SubjectType
from app.models.osint import OsintArtifact, OsintCase, OsintLookup
from app.models.user import User
from app.schemas.intelligence import IntelArtifact, LookupIn
from app.schemas.osint import (
    ArtifactOut,
    CaseCreate,
    CaseOut,
    LookupOut,
    ManualArtifactIn,
    SourceOut,
)
from app.services import audit, intelligence
from app.services.auth import get_current_user
from app.services.intel_targets import normalize_target
from app.services.osint import save_artifact

router = APIRouter(prefix="/osint/cases", tags=["osint"])


async def _owned(
    session: AsyncSession, owner_id: str, case_id: str, *, lock: bool = False
) -> OsintCase:
    query = select(OsintCase).where(OsintCase.id == case_id, OsintCase.owner_id == owner_id)
    if lock:
        query = query.with_for_update()
    case = await session.scalar(query)
    if case is None:
        raise HTTPException(404, "Кейс не найден")
    return case


def _summaries():
    count_artifacts = (
        select(func.count(OsintArtifact.id))
        .where(
            OsintArtifact.case_id == OsintCase.id,
        )
        .correlate(OsintCase)
        .scalar_subquery()
    )
    count_lookups = (
        select(func.count(OsintLookup.id))
        .where(
            OsintLookup.case_id == OsintCase.id,
        )
        .correlate(OsintCase)
        .scalar_subquery()
    )
    return select(OsintCase, count_artifacts, count_lookups)


def _summary(row) -> CaseOut:
    case, artifact_count, lookup_count = row
    return CaseOut.model_validate(case).model_copy(
        update={
            "artifact_count": artifact_count,
            "lookup_count": lookup_count,
        }
    )


@router.get("", response_model=list[CaseOut])
async def list_cases(
    q: str = Query("", max_length=500),
    subject_type: SubjectType | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CaseOut]:
    stmt = _summaries().where(OsintCase.owner_id == user.id)
    if q.strip():
        stmt = stmt.where(
            func.lower(OsintCase.subject).contains(q.strip().lower(), autoescape=True)
        )
    if subject_type:
        stmt = stmt.where(OsintCase.subject_type == subject_type)
    rows = await session.execute(
        stmt.order_by(
            OsintCase.created_at.desc(),
            OsintCase.id,
        )
        .limit(limit)
        .offset(offset)
    )
    return [_summary(row) for row in rows]


@router.post("", response_model=CaseOut, status_code=201)
async def create_case(
    body: CaseCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OsintCase:
    case = OsintCase(owner_id=user.id, **body.model_dump())
    session.add(case)
    await session.flush()
    await audit.record(session, actor=user.id, action="osint.case_create", target=case.id)
    await session.commit()
    return case


@router.get("/{case_id}", response_model=CaseOut)
async def get_case(
    case_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CaseOut:
    row = (
        await session.execute(
            _summaries().where(
                OsintCase.id == case_id,
                OsintCase.owner_id == user.id,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Кейс не найден")
    return _summary(row)


@router.delete("/{case_id}", status_code=204)
async def delete_case(
    case_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    case = await _owned(session, user.id, case_id, lock=True)
    # Explicit children also support local SQLite deployments without FK pragmas.
    await session.execute(delete(OsintArtifact).where(OsintArtifact.case_id == case_id))
    await session.execute(delete(OsintLookup).where(OsintLookup.case_id == case_id))
    await session.delete(case)
    await audit.record(session, actor=user.id, action="osint.case_delete", target=case_id)
    await session.commit()


@router.get("/{case_id}/artifacts", response_model=list[ArtifactOut])
async def artifacts(
    case_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[OsintArtifact]:
    await _owned(session, user.id, case_id)
    return list(
        await session.scalars(
            select(OsintArtifact)
            .where(
                OsintArtifact.case_id == case_id,
            )
            .order_by(OsintArtifact.created_at.desc(), OsintArtifact.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.post("/{case_id}/artifacts", response_model=ArtifactOut, status_code=201)
async def add_artifact(
    case_id: str,
    body: ManualArtifactIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OsintArtifact:
    case = await _owned(session, user.id, case_id, lock=True)
    artifact, created = await save_artifact(
        session,
        case_id,
        "manual",
        case.subject,
        IntelArtifact(kind="note", **body.model_dump()),
    )
    await audit.record(
        session,
        actor=user.id,
        action="osint.artifact_add",
        target=case_id,
        meta={"artifact_id": artifact.id, "duplicate": not created},
    )
    await session.commit()
    return artifact


@router.get("/{case_id}/sources", response_model=list[SourceOut])
async def sources(
    case_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SourceOut]:
    await _owned(session, user.id, case_id)
    rows = await session.execute(
        select(
            OsintArtifact.provider,
            OsintArtifact.source_url,
            func.count(OsintArtifact.id),
        )
        .where(OsintArtifact.case_id == case_id)
        .group_by(
            OsintArtifact.provider,
            OsintArtifact.source_url,
        )
        .order_by(OsintArtifact.provider, OsintArtifact.source_url)
        .limit(limit)
        .offset(offset)
    )
    return [SourceOut(provider=p, source_url=url, artifact_count=n) for p, url, n in rows]


@router.get("/{case_id}/lookups", response_model=list[LookupOut])
async def lookups(
    case_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[OsintLookup]:
    await _owned(session, user.id, case_id)
    return list(
        await session.scalars(
            select(OsintLookup)
            .where(
                OsintLookup.case_id == case_id,
            )
            .order_by(OsintLookup.created_at.desc(), OsintLookup.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.post("/{case_id}/lookups", response_model=LookupOut, status_code=201)
async def lookup(
    case_id: str,
    body: LookupIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OsintLookup:
    case = await _owned(session, user.id, case_id)
    target = body.target
    if target is None:
        if case.subject_type != SubjectType.domain:
            raise HTTPException(422, "Для человека или компании укажите связанный домен/IP")
        try:
            target = normalize_target(case.subject)
        except ValueError as exc:
            raise HTTPException(422, "Укажите корректный домен/IP для этого кейса") from exc

    result = await intelligence.run_lookup(session, user.id, body.provider, target)
    # The case may have been deleted during the network call. Lock only while
    # persisting, not while waiting for a provider, to keep unrelated actions fast.
    await _owned(session, user.id, case_id, lock=True)
    run = OsintLookup(
        case_id=case_id,
        provider=body.provider,
        target=target,
        status=result.status,
        error_code=result.error_code,
        error=result.error,
        artifact_count=0,
        duplicate_count=0,
    )
    session.add(run)
    if result.status == "ok":
        for artifact in result.artifacts:
            _, created = await save_artifact(session, case_id, body.provider, target, artifact)
            run.artifact_count += int(created)
            run.duplicate_count += int(not created)
    await audit.record(
        session,
        actor=user.id,
        action="osint.lookup",
        target=case_id,
        meta={
            "provider": body.provider,
            "subject": target,
            "status": result.status,
            "error_code": result.error_code,
            "added": run.artifact_count,
            "duplicates": run.duplicate_count,
            "passive": True,
        },
    )
    await session.commit()
    return run
