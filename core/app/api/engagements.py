"""Engagements, Scope и authorized-gate (спец. §5.4, §7.1, §7.2)."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.agent import AgentConfig
from app.models.enums import VenueMode
from app.models.pentest import Engagement, Scope, Server, Venue
from app.models.user import User
from app.schemas.pentest import (
    EngagementCreate,
    EngagementOut,
    EngagementUpdate,
    OffensiveUpdate,
    ScopeCheckRequest,
    ScopeCheckResult,
    ScopeOut,
    ScopeUpdate,
    VenueOut,
    VenueUpdate,
)
from app.schemas.pentest_authorization import (
    AuthorizationAccept,
    AuthorizationCreate,
    AuthorizationDefaults,
)
from app.services import audit
from app.services import pentest_authorization as authorization
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
    document = await authorization.read(session, e)
    out.authorized = document["valid"]
    out.offensive_enabled = e.offensive_enabled and out.authorized
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


async def _write_owned(session, user, eid):
    # Start with a write lock rather than upgrading a stale SQLite read snapshot.
    owner_id = user.id
    await session.rollback()
    result = await session.execute(update(Engagement).where(
        Engagement.id == eid, Engagement.owner_id == owner_id).values(target=Engagement.target))
    if result.rowcount != 1:
        raise HTTPException(404, "Engagement не найден")
    return await session.get(Engagement, eid, populate_existing=True)


@router.get("/authorization/defaults")
async def authorization_defaults(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await authorization.defaults(session, user.id)


@router.put("/authorization/defaults")
async def save_authorization_defaults(body: AuthorizationDefaults, user: User = Depends(get_current_user),
                                      session: AsyncSession = Depends(get_session)):
    config = await session.scalar(select(AgentConfig).where(AgentConfig.owner_id == user.id))
    if config is None:
        config = AgentConfig(owner_id=user.id)
        session.add(config)
    config.context = {**(config.context or {}), "authorization_defaults": body.model_dump()}
    await session.commit()
    return body


@router.get("/{eid}/authorization")
async def get_authorization(eid: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await authorization.read(session, await _owned(session, user, eid))


@router.put("/{eid}/authorization")
async def create_authorization(eid: str, body: AuthorizationCreate, user: User = Depends(get_current_user),
                               session: AsyncSession = Depends(get_session)):
    e = await _write_owned(session, user, eid)
    if body.expires_at and body.expires_at <= datetime.now(UTC):
        raise HTTPException(400, "Срок действия должен быть в будущем")
    await authorization.create(session, e, body.model_dump(exclude={"content", "expires_at"}),
                               content=body.content, expires_at=body.expires_at)
    await session.commit()
    return await authorization.read(session, e)


@router.post("/{eid}/authorization/accept")
async def accept_authorization(eid: str, body: AuthorizationAccept, user: User = Depends(get_current_user),
                               session: AsyncSession = Depends(get_session)):
    e = await _write_owned(session, user, eid)
    document = await authorization.latest(session, eid)
    if document is None or document.content_sha256 != body.content_sha256:
        raise HTTPException(409, "Документ изменился: перечитайте текущую версию")
    await authorization.accept(session, e, document)
    await session.commit()
    return await authorization.read(session, e)


@router.post("/{eid}/authorization/revoke")
async def revoke_authorization(eid: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    e = await _write_owned(session, user, eid)
    document = await authorization.latest(session, eid)
    if document:
        document.status = "revoked"
    e.authorized, e.offensive_enabled = False, False
    await audit.record(session, actor=e.owner_id, action="authorization.revoke", target=eid)
    await session.commit()
    return await authorization.read(session, e)


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
    if body.target is not None and body.target != e.target:
        e.authorized, e.offensive_enabled = False, False
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
    e = await _write_owned(session, user, eid)
    sc = await _scope(session, eid)
    if sc is None:
        sc = Scope(engagement_id=eid)
        session.add(sc)
    sc.allow = body.allow
    sc.deny = body.deny
    # Изменение scope сбрасывает подтверждение и авторизацию (безопасность).
    sc.confirmed = False
    e.offensive_enabled = False
    if e.authorized:
        e.authorized = False
        await audit.record(session, actor=e.owner_id, action="engagement.deauthorized",
                           target=eid, note="scope изменён")
    await session.flush()
    await authorization.generate_from_scope(session, e)
    await audit.record(session, actor=e.owner_id, action="scope.update", target=eid,
                       meta={"allow": body.allow, "deny": body.deny})
    await session.commit()
    return await _to_out(session, e)


@router.post("/{eid}/scope/confirm", response_model=EngagementOut)
async def confirm_scope(
    eid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EngagementOut:
    e = await _write_owned(session, user, eid)
    sc = await _scope(session, eid)
    if sc is None or not (sc.allow or sc.deny):
        raise HTTPException(status_code=400, detail="Scope пуст — нечего подтверждать")
    sc.confirmed = True
    await session.flush()
    if await authorization.latest(session, eid) is None:
        await authorization.generate_from_scope(session, e)
    await audit.record(session, actor=e.owner_id, action="scope.confirm", target=eid)
    await session.commit()
    return await _to_out(session, e)


@router.post("/{eid}/authorize", response_model=EngagementOut)
async def set_authorized(
    eid: str,
    authorized: bool = True,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EngagementOut:
    """Compatibility endpoint: explicitly accepts an existing separate declaration."""
    e = await _write_owned(session, user, eid)
    document = await authorization.latest(session, eid)
    if authorized:
        sc = await _scope(session, eid)
        if sc is None or not sc.confirmed:
            raise HTTPException(400, "Нельзя авторизовать: сначала подтвердите Scope")
        if document is None:
            raise HTTPException(400, "Сначала оформите отдельную декларацию авторизации")
        await authorization.accept(session, e, document)
    else:
        e.authorized, e.offensive_enabled = False, False
        if document:
            document.status = "revoked"
        await audit.record(session, actor=e.owner_id, action="engagement.deauthorize", target=eid)
    await session.commit()
    return await _to_out(session, e)


@router.put("/{eid}/offensive", response_model=EngagementOut)
async def set_offensive(
    eid: str, body: OffensiveUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EngagementOut:
    e = await _owned(session, user, eid)
    sc = await _scope(session, eid)
    if body.enabled and (not (await authorization.read(session, e))["valid"] or sc is None or not sc.confirmed):
        raise HTTPException(status_code=400, detail="Сначала подтвердите scope и авторизуйте engagement")
    e.offensive_enabled = body.enabled
    await audit.record(session, actor=user.id, action="engagement.offensive", target=eid,
                       meta={"enabled": body.enabled})
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
        if not (await authorization.read(session, e))["valid"] or sc is None or not sc.confirmed:
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
