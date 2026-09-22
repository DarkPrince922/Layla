"""Findings, импорт Acunetix HTML и отчёты (спец. §5.4)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.enums import FindingSource, FindingStatus, Severity
from app.models.pentest import Engagement, Finding, Report
from app.models.user import User
from app.schemas.pentest import FindingCreate, FindingOut, FindingUpdate, ReportOut
from app.services import acunetix, audit
from app.services.auth import get_current_user

router = APIRouter(prefix="/engagements/{eid}", tags=["pentest"])

_SEV_ORDER = {
    Severity.critical: 0, Severity.high: 1, Severity.medium: 2,
    Severity.low: 3, Severity.info: 4,
}


async def _owned_engagement(session: AsyncSession, user: User, eid: str) -> Engagement:
    e = await session.get(Engagement, eid)
    if e is None or e.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Engagement не найден")
    return e


@router.get("/findings", response_model=list[FindingOut])
async def list_findings(
    eid: str,
    severity: Severity | None = Query(default=None),
    status: FindingStatus | None = Query(default=None),
    sort: str = Query(default="risk"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[FindingOut]:
    await _owned_engagement(session, user, eid)
    stmt = select(Finding).where(Finding.engagement_id == eid)
    if severity:
        stmt = stmt.where(Finding.severity == severity)
    if status:
        stmt = stmt.where(Finding.status == status)
    rows = list(await session.scalars(stmt))
    if sort == "risk":
        rows.sort(key=lambda f: _SEV_ORDER.get(f.severity, 9))
    return [FindingOut.model_validate(f) for f in rows]


@router.post("/findings", response_model=FindingOut, status_code=201)
async def create_finding(
    eid: str,
    body: FindingCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FindingOut:
    await _owned_engagement(session, user, eid)
    f = Finding(
        engagement_id=eid,
        severity=body.severity, type=body.type, url=body.url, method=body.method,
        param=body.param, title=body.title, description=body.description,
        source=body.source, created_by=user.id,
        dedup_key=acunetix.dedup_key(body.model_dump()),
    )
    session.add(f)
    await session.commit()
    return FindingOut.model_validate(f)


@router.patch("/findings/{fid}", response_model=FindingOut)
async def update_finding(
    eid: str,
    fid: str,
    body: FindingUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FindingOut:
    await _owned_engagement(session, user, eid)
    f = await session.get(Finding, fid)
    if f is None or f.engagement_id != eid:
        raise HTTPException(status_code=404, detail="Находка не найдена")
    for field_, v in body.model_dump(exclude_unset=True).items():
        setattr(f, field_, v)
    await session.commit()
    return FindingOut.model_validate(f)


@router.delete("/findings/{fid}", status_code=204)
async def delete_finding(
    eid: str,
    fid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _owned_engagement(session, user, eid)
    f = await session.get(Finding, fid)
    if f is None or f.engagement_id != eid:
        raise HTTPException(status_code=404, detail="Находка не найдена")
    await session.delete(f)
    await session.commit()


@router.post("/import/acunetix", response_model=ReportOut, status_code=201)
async def import_acunetix(
    eid: str,
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReportOut:
    """Импорт HTML-отчёта Acunetix с дедупликацией по (type,url,param)."""
    await _owned_engagement(session, user, eid)
    html = body.get("html", "")
    if not html:
        raise HTTPException(status_code=400, detail="Пустой отчёт")

    parsed = acunetix.parse_html(html)
    sha = acunetix.sha256_of(html)

    # Существующие ключи дедупа в этом engagement.
    existing = {
        f.dedup_key
        for f in await session.scalars(
            select(Finding).where(Finding.engagement_id == eid)
        )
        if f.dedup_key
    }
    imported = 0
    dupes = 0
    seen: set[str] = set()
    for item in parsed.findings:
        key = acunetix.dedup_key(item)
        if key in existing or key in seen:
            dupes += 1
            continue
        seen.add(key)
        session.add(
            Finding(
                engagement_id=eid,
                severity=item["severity"], type=item["type"], url=item.get("url"),
                param=item.get("param"), method=item.get("method"),
                source=FindingSource.scanner, dedup_key=key, created_by=user.id,
            )
        )
        imported += 1

    stats = {"declared": parsed.declared, "imported": imported, "dupes": dupes}
    report = Report(
        engagement_id=eid, format="acunetix-import", sha256_source=sha,
        stats=stats, status="ok" if parsed.declared else "failed",
    )
    session.add(report)
    await audit.record(
        session, actor=user.id, action="report.import.acunetix", target=eid,
        meta={**stats, "sha256": sha},
    )
    await session.commit()
    return ReportOut.model_validate(report)


@router.post("/reports/generate", response_model=ReportOut, status_code=201)
async def generate_report(
    eid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReportOut:
    """Сгенерировать markdown-отчёт по engagement (сводка находок)."""
    e = await _owned_engagement(session, user, eid)
    findings = list(await session.scalars(select(Finding).where(Finding.engagement_id == eid)))
    findings.sort(key=lambda f: _SEV_ORDER.get(f.severity, 9))

    by_sev: dict[str, int] = {}
    for f in findings:
        sv = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
        by_sev[sv] = by_sev.get(sv, 0) + 1

    lines = [
        f"# Отчёт по engagement: {e.target}",
        f"_Сгенерирован: {dt.datetime.now(tz=dt.timezone.utc).isoformat()}_",
        "",
        f"Всего находок: {len(findings)}",
        "",
        "## Сводка по важности",
    ]
    for sv, n in by_sev.items():
        lines.append(f"- {sv}: {n}")
    lines.append("\n## Находки\n")
    for f in findings:
        sv = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
        lines.append(f"### [{sv}] {f.type} — {f.title or ''}")
        if f.url:
            lines.append(f"- URL: `{f.method or ''} {f.url}`")
        if f.param:
            lines.append(f"- Параметр: `{f.param}`")
        if f.description:
            lines.append(f"\n{f.description}\n")

    markdown = "\n".join(lines)
    report = Report(
        engagement_id=eid, format="markdown",
        stats={"findings": len(findings), "by_severity": by_sev},
        status="ok",
    )
    report.sha256_source = acunetix.sha256_of(markdown)
    session.add(report)
    await audit.record(session, actor=user.id, action="report.generate", target=eid)
    await session.commit()
    out = ReportOut.model_validate(report)
    return out


@router.get("/reports", response_model=list[ReportOut])
async def list_reports(
    eid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ReportOut]:
    await _owned_engagement(session, user, eid)
    rows = await session.scalars(
        select(Report).where(Report.engagement_id == eid).order_by(Report.created_at.desc())
    )
    return [ReportOut.model_validate(r) for r in rows]
