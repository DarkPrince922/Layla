"""Owner-authenticated OAuth UI and downloads for Code MCP."""
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session, get_sessionmaker
from app.models.chat import Chat
from app.models.code_mcp import CodeMcpArtifact, CodeMcpAuth
from app.models.enums import Domain
from app.models.mcp import McpServer
from app.models.user import User
from app.services import code_mcp_oauth
from app.services.auth import get_current_user

router = APIRouter(prefix="/code/mcp", tags=["code-mcp"])


def base_url(request):
    settings = get_settings()
    base = (settings.public_url or ("https://" + settings.public_host if settings.env == "prod" else str(request.base_url))).rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path or (settings.env == "prod" and parsed.scheme != "https")):
        raise HTTPException(422, "Укажите LAYLA_PUBLIC_URL как внешний origin Layla")
    return base


async def owned(session, user, server_id):
    server = await session.get(McpServer, server_id)
    if server is None or server.owner_id != user.id:
        raise HTTPException(404, "MCP-сервер не найден")
    return server


@router.get("/client-metadata")
async def client_metadata(request: Request):
    base = base_url(request)
    return {"client_id": base + "/api/code/mcp/client-metadata", "client_name": "Layla Code",
            "redirect_uris": [base + "/api/code/mcp/oauth/callback"],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none"}


@router.get("/oauth")
async def auth_status(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    result = {}
    for row in await session.scalars(select(CodeMcpAuth).where(CodeMcpAuth.owner_id == user.id)):
        saved = code_mcp_oauth.decrypt(row.tokens_secret)
        connected = bool(saved and (not saved.get("expires_at") or saved["expires_at"] > time.time() or saved.get("refresh_token")))
        pending = bool(row.state_hash and row.expires_at and row.expires_at.timestamp() > time.time())
        result[row.server_id] = "connected" if connected else "pending" if pending else "authorization_required"
    return result


@router.post("/{server_id}/oauth")
async def authorize(server_id: str, body: code_mcp_oauth.Start, request: Request,
                    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                    maker=Depends(get_sessionmaker)):
    server = await owned(session, user, server_id)
    base = base_url(request)
    try:
        return await code_mcp_oauth.start(maker, server, body, base + "/api/code/mcp/oauth/callback",
                                         base + "/api/code/mcp/client-metadata")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "Не удалось подключиться к OAuth-серверу") from exc


@router.get("/oauth/callback")
async def oauth_callback(state: str, code: str | None = None, iss: str | None = None, error: str | None = None,
                         user: User = Depends(get_current_user), maker=Depends(get_sessionmaker)):
    if len(state) > 200 or (code and len(code) > 8000) or (iss and len(iss) > 4096):
        raise HTTPException(400, "Некорректный OAuth callback")
    try:
        await code_mcp_oauth.callback(maker, user.id, state, code, iss, error)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "OAuth callback не завершён; подключите сервер заново") from exc
    return RedirectResponse("/settings/integrations?mcp_oauth=connected", status_code=303,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.delete("/{server_id}/oauth")
async def disconnect(server_id: str, user: User = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker)):
    server = await owned(session, user, server_id)
    await code_mcp_oauth.revoke(maker, server)
    return {"disconnected": True}


@router.get("/artifacts/{artifact_id}")
async def artifact(artifact_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    row = await session.get(CodeMcpArtifact, artifact_id)
    chat = await session.get(Chat, row.chat_id) if row and row.owner_id == user.id else None
    if chat is None or chat.owner_id != user.id or chat.domain != Domain.code or chat.deleted_at:
        raise HTTPException(404, "Файл не найден")
    return Response(row.data, media_type=row.mime_type, headers={"Content-Disposition": 'attachment; filename="mcp-result"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
