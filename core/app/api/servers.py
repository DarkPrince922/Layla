"""Attack-box серверы (SSH, key-only) и проверка egress-маршрута (спец. §5.4, §7.4)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.enums import EgressRoute, ServerAuth
from app.models.pentest import Server
from app.models.user import User
from app.schemas.pentest import ServerCreate, ServerOut
from app.security import crypto
from app.services import audit, egress, ssh_exec
from app.services.auth import get_current_user

router = APIRouter(prefix="/servers", tags=["pentest"])


def _to_out(s: Server) -> ServerOut:
    out = ServerOut.model_validate(s)
    out.has_secret = bool(s.secret_ref)
    out.has_key = out.has_secret
    return out


async def _owned(session: AsyncSession, user: User, sid: str) -> Server:
    s = await session.get(Server, sid)
    if s is None or s.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Сервер не найден")
    return s


@router.get("", response_model=list[ServerOut])
async def list_servers(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ServerOut]:
    rows = await session.scalars(select(Server).where(Server.owner_id == user.id))
    return [_to_out(s) for s in rows]


@router.post("", response_model=ServerOut, status_code=201)
async def create_server(
    body: ServerCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ServerOut:
    secret = body.password if body.auth == ServerAuth.password else body.private_key
    s = Server(
        owner_id=user.id,
        engagement_id=body.engagement_id,
        host=body.host,
        port=body.port,
        user=body.user,
        auth=body.auth.value,
        egress_route=body.egress_route,
        secret_ref=crypto.encrypt(secret) if secret else None,
    )
    session.add(s)
    await audit.record(session, actor=user.id, action="server.add",
                       target=f"{body.user}@{body.host}", meta={"auth": body.auth.value})
    await session.commit()
    return _to_out(s)


@router.delete("/{sid}", status_code=204)
async def delete_server(
    sid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    s = await _owned(session, user, sid)
    await session.delete(s)
    await session.commit()


@router.post("/{sid}/test-connection")
async def test_connection(
    sid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Проверить SSH-доступ к серверу и закрепить ключ хоста (TOFU) при первом успехе."""
    s = await _owned(session, user, sid)
    if not s.secret_ref:
        raise HTTPException(status_code=400, detail="У сервера не задан ключ или пароль")
    cfg = ssh_exec.SSHConfig(
        host=s.host, port=s.port, user=s.user, auth=s.auth,
        secret=crypto.decrypt(s.secret_ref), host_key=s.host_key,
    )
    try:
        result = await ssh_exec.probe(cfg)
    except ssh_exec.SSHError as exc:
        await audit.record(session, actor=user.id, action="server.test_connection",
                           target=f"{s.user}@{s.host}", meta={"ok": False})
        await session.commit()
        return {"ok": False, "reason": str(exc)}
    pinned = False
    if result.host_key and s.host_key != result.host_key:
        s.host_key = result.host_key
        pinned = True
    await audit.record(session, actor=user.id, action="server.test_connection",
                       target=f"{s.user}@{s.host}", meta={"ok": True, "pinned": pinned})
    await session.commit()
    return {"ok": True, "reason": f"Подключение успешно ({s.user}@{s.host}:{s.port})",
            "pinned_host_key": pinned}


@router.post("/{sid}/test-route")
async def test_route(
    sid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Проверить egress-маршрут сервера (fail-closed для Tor/Proxy)."""
    s = await _owned(session, user, sid)
    settings = get_settings()
    route = egress.effective_route(s.egress_route, None, EgressRoute.direct)

    def probe(r: EgressRoute) -> bool:
        addr = settings.tor_addr if r == EgressRoute.tor else settings.proxy_addr
        if not addr or ":" not in addr:
            return False
        host, _, port = addr.rpartition(":")
        return egress.tcp_probe(host, int(port))

    try:
        decision = egress.assert_egress_available(route, probe=probe)
    except egress.EgressBlocked as exc:
        return {"ok": False, "route": route.value, "reason": str(exc)}
    return {"ok": True, "route": decision.route.value, "reason": decision.reason}
