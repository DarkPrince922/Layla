"""Реестр MCP-серверов и проверка подключения (спец. §5.8)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.mcp import McpServer
from app.models.user import User
from app.schemas.mcp import (
    McpServerCreate,
    McpServerOut,
    McpServerUpdate,
    McpTestResult,
    McpToolInfo,
)
from app.services import audit, mcp_client
from app.services.auth import get_current_user
from app.services.mcp_secrets import decrypt_env, encrypt_env

router = APIRouter(prefix="/mcp/servers", tags=["mcp"])


def _to_out(s: McpServer) -> McpServerOut:
    out = McpServerOut.model_validate(s)
    try:
        out.env_keys = list(decrypt_env(s))
    except ValueError:
        out.env_keys = []
    return out


async def _owned(session: AsyncSession, user: User, server_id: str) -> McpServer:
    srv = await session.get(McpServer, server_id)
    if srv is None or srv.owner_id != user.id:
        raise HTTPException(status_code=404, detail="MCP-сервер не найден")
    return srv


@router.get("", response_model=list[McpServerOut])
async def list_servers(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[McpServerOut]:
    rows = await session.scalars(select(McpServer).where(McpServer.owner_id == user.id))
    return [_to_out(s) for s in rows]


@router.post("", response_model=McpServerOut, status_code=201)
async def create_server(
    body: McpServerCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> McpServerOut:
    srv = McpServer(
        owner_id=user.id,
        name=body.name,
        transport=body.transport,
        command=body.command,
        url=body.url,
        enabled=body.enabled,
        personas=body.personas,
        env={},
        env_secret_ref=encrypt_env(body.env),
    )
    session.add(srv)
    await audit.record(session, actor=user.id, action="mcp.register", target=body.name)
    await session.commit()
    return _to_out(srv)


@router.patch("/{server_id}", response_model=McpServerOut)
async def update_server(
    server_id: str,
    body: McpServerUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> McpServerOut:
    srv = await _owned(session, user, server_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(srv, field, value)
    await session.commit()
    return _to_out(srv)


@router.delete("/{server_id}", status_code=204)
async def delete_server(
    server_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    srv = await _owned(session, user, server_id)
    await session.delete(srv)
    await session.commit()


@router.post("/{server_id}/test", response_model=McpTestResult)
async def test_server(
    server_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> McpTestResult:
    """Проверить подключение: инициализировать сессию и получить список инструментов."""
    srv = await _owned(session, user, server_id)
    if not srv.enabled:
        return McpTestResult(ok=False, error="MCP-сервер выключен")
    try:
        tools = await mcp_client.list_tools(
            transport=srv.transport.value if hasattr(srv.transport, "value") else str(srv.transport),
            command=srv.command,
            url=srv.url,
            env=decrypt_env(srv),
        )
        return McpTestResult(ok=True, tools=[McpToolInfo(**t) for t in tools])
    except RuntimeError as exc:
        return McpTestResult(ok=False, error=str(exc))
    except ValueError:
        return McpTestResult(ok=False, error="Не удалось прочитать настройки MCP-сервера")
