"""Engagements, Scope и authorized-gate (спец. §5.4, §7.1, §7.2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.pentest import Engagement, Scope, Venue
from app.models.enums import VenueMode
from app.models.user import User
from app.models.pentest import Server
from app.schemas.pentest import (
    EngagementCreate,
    EngagementOut,
    EngagementUpdate,
    ScopeCheckRequest,
    ScopeCheckResult,
    ScopeOut,
    ScopeUpdate,
    VenueOut,
    VenueUpdate,
)
from app.services import audit
from app.services import scope as scope_svc
from app.services.auth import get_current_user

router = APIRouter(prefix="/engagements", tags=["pentest"])


async def _owned(session: AsyncSession, user: User, eid: str) -> Engagement:
    e = await session.get(Engagement, eid)
    if e is None or e.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Engagement не найден")
    return e


async def _scope(session: AsyncSession, eid: str) -> Scope | None:
    return await session.scalar(select(Scope).where(Scope.engagement_id == eid))


async def _venue(session: AsyncSession, eid: str) -> Venue | None:
    return await session.scalar(select(Venue).where(Venue.engagement_id == eid))


async def _to_out(session: AsyncSession, e: Engagement) -> EngagementOut:
    out = EngagementOut.model_validate(e)
    sc = await _scope(session, e.id)
    if sc is not None:
        out.scope = ScopeOut(allow=sc.allow or [], deny=sc.deny or [], confirmed=sc.confirmed)
    vn = await _venue(session, e.id)
    if vn is not None:
        out.venue = VenueOut(mode=vn.mode, attack_box_id=vn.attack_box_id, egress_route=vn.egress_route)
    return out


@router.get("", response_model=list[EngagementOut])
async def list_engagements(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[EngagementOut]:
    rows = await session.scalars(
        select(Engagement).where(Engagement.owner_id == user.id).order_by(
            Engagement.created_at.desc()
        )
    )
    return [await _to_out(session, e) for e in rows]


@router.post("", response_model=EngagementOut, status_code=201)
async def create_engagement(
    body: EngagementCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EngagementOut:
    e = Engagement(owner_id=user.id, target=body.target, workspace_id=body.workspace_id)
    session.add(e)
    await session.flush()
    # Безопасные значения по умолчанию: пустой scope, venue = analysis_only.
    session.add(Scope(engagement_id=e.id, allow=[], deny=[], confirmed=False))
    session.add(Venue(engagement_id=e.id, mode=VenueMode.analysis_only))
    await audit.record(session, actor=user.id, action="engagement.create", target=body.target)
    await session.commit()
    return await _to_out(session, e)


@router.get("/{eid}", response_model=EngagementOut)
async def get_engagement(
    eid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EngagementOut:
    e = await _owned(session, user, eid)
    return await _to_out(session, e)


@router.patch("/{eid}", response_model=EngagementOut)
async def update_engagement(
    eid: str,
    body: EngagementUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EngagementOut:
    e = await _owned(session, user, eid)
    for f, v in body.model_dump(exclude_unset=True).items():
        setattr(e, f, v)
    await session.commit()
    return await _to_out(session, e)


@router.delete("/{eid}", status_code=204)
async def delete_engagement(
    eid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    e = await _owned(session, user, eid)
    await session.delete(e)
    await session.commit()


@router.put("/{eid}/scope", response_model=EngagementOut)
async def set_scope(
    eid: str,
    body: ScopeUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EngagementOut:
    e = await _owned(session, user, eid)
    sc = await _scope(session, eid)
    if sc is None:
        sc = Scope(engagement_id=eid)
        session.add(sc)
    sc.allow = body.allow
    sc.deny = body.deny
    # Изменение scope сбрасывает подтверждение и авторизацию (безопасность).
    sc.confirmed = False
    if e.authorized:
        e.authorized = False
        await audit.record(session, actor=user.id, action="engagement.deauthorized",
                           target=eid, note="scope изменён")
    await audit.record(session, actor=user.id, action="scope.update", target=eid,
                       meta={"allow": body.allow, "deny": body.deny})
    await session.commit()
    return await _to_out(session, e)


@router.post("/{eid}/scope/confirm", response_model=EngagementOut)
async def confirm_scope(
    eid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EngagementOut:
    e = await _owned(session, user, eid)
    sc = await _scope(session, eid)
    if sc is None or not (sc.allow or sc.deny):
        raise HTTPException(status_code=400, detail="Scope пуст — нечего подтверждать")
    sc.confirmed = True
    await audit.record(session, actor=user.id, action="scope.confirm", target=eid)
    await session.commit()
    return await _to_out(session, e)


@router.post("/{eid}/authorize", response_model=EngagementOut)
async def set_authorized(
    eid: str,
    authorized: bool = True,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EngagementOut:
    """Authorized-workspace gate (§7.1): включается явным действием оператора.

    Требует подтверждённого scope. Каждое изменение фиксируется в AuditLog.
    """
    e = await _owned(session, user, eid)
    if authorized:
        sc = await _scope(session, eid)
        if sc is None or not sc.confirmed:
            raise HTTPException(
                status_code=400,
                detail="Нельзя авторизовать: сначала подтвердите Scope",
            )
    e.authorized = authorized
    await audit.record(
        session, actor=user.id,
        action="engagement.authorize" if authorized else "engagement.deauthorize",
        target=eid,
    )
    await session.commit()
    return await _to_out(session, e)


@router.post("/{eid}/scope/check", response_model=ScopeCheckResult)
async def check_scope(
    eid: str,
    body: ScopeCheckRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ScopeCheckResult:
    """Проверить конкретную цель против scope (для UI и предпросмотра)."""
    await _owned(session, user, eid)
    sc = await _scope(session, eid)
    allow = sc.allow if sc else []
    deny = sc.deny if sc else []
    d = scope_svc.check_target(body.target, allow or [], deny or [])
    return ScopeCheckResult(allowed=d.allowed, reason=d.reason, matched=d.matched)


@router.put("/{eid}/venue", response_model=EngagementOut)
async def set_venue(
    eid: str,
    body: VenueUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EngagementOut:
    """Выбор площадки выполнения (спец. §5.4, §7.1, §7.3).

    Активные режимы (attack_box / this_machine) требуют authorized=True И
    подтверждённого scope. this_machine раскрывает IP оператора — включается
    осознанно.
    """
    e = await _owned(session, user, eid)
    vn = await _venue(session, eid)
    if vn is None:
        vn = Venue(engagement_id=eid)
        session.add(vn)

    if body.mode in (VenueMode.attack_box, VenueMode.this_machine):
        sc = await _scope(session, eid)
        if not e.authorized or sc is None or not sc.confirmed:
            raise HTTPException(
                status_code=400,
                detail="Активная площадка требует авторизованного engagement и подтверждённого scope",
            )
    if body.mode == VenueMode.attack_box:
        if not body.attack_box_id:
            raise HTTPException(status_code=400, detail="Не выбран attack box")
        srv = await session.get(Server, body.attack_box_id)
        if srv is None or srv.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Attack box не найден")

    vn.mode = body.mode
    vn.attack_box_id = body.attack_box_id if body.mode == VenueMode.attack_box else None
    vn.egress_route = body.egress_route
    await audit.record(
        session, actor=user.id, action="venue.set", target=eid,
        meta={"mode": body.mode.value, "egress": body.egress_route.value},
    )
    await session.commit()
    return await _to_out(session, e)
