"""Intelligence API key management. No endpoint returns a secret or ciphertext."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.enums import IntelProvider
from app.models.mcp import IntelKey
from app.models.user import User
from app.schemas.intelligence import IntelKeyIn, IntelProviderName, IntelProviderOut
from app.schemas.mcp import McpTestResult, McpToolInfo
from app.security import crypto
from app.services import audit, intel_api, intelligence, mcp_client
from app.services.auth import get_current_user

router = APIRouter(prefix="/integrations/intelligence", tags=["intelligence"])


async def _provider_out(session: AsyncSession, owner_id: str, provider: str) -> IntelProviderOut:
    row = await intelligence.get_key(session, owner_id, provider)
    masked = None
    if row:
        try:
            masked = crypto.mask(crypto.decrypt(row.secret_ref))
        except ValueError:
            masked = "••••"
    return IntelProviderOut(
        provider=provider,
        **intel_api.PROVIDERS[provider],
        tool_name=f"{provider}_lookup",
        configured=row is not None,
        key_masked=masked,
    )


@router.get("", response_model=list[IntelProviderOut])
async def providers(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> list[IntelProviderOut]:
    return [await _provider_out(session, user.id, p) for p in intel_api.PROVIDERS]


@router.post("/test", response_model=McpTestResult)
async def test_mcp(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> McpTestResult:
    """Local MCP handshake only; does not spend provider credits or send a subject."""
    try:
        tools = await mcp_client.list_tools(
            transport="stdio",
            command=intelligence.MCP_COMMAND,
            url=None,
        )
        result = McpTestResult(ok=True, tools=[McpToolInfo(**tool) for tool in tools])
    except RuntimeError:
        result = McpTestResult(ok=False, error="Не удалось запустить встроенный MCP-сервер")
    await audit.record(session, actor=user.id, action="intel.mcp_test", meta={"ok": result.ok})
    await session.commit()
    return result


@router.put("/{provider}", response_model=IntelProviderOut)
async def set_key(
    provider: IntelProviderName,
    body: IntelKeyIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> IntelProviderOut:
    # Serialize a user's key changes (including the first insert) on Postgres.
    await session.scalar(select(User).where(User.id == user.id).with_for_update())
    row = await intelligence.get_key(session, user.id, provider)
    ciphertext = crypto.encrypt(body.api_key.get_secret_value())
    if row:
        row.secret_ref = ciphertext
    else:
        session.add(
            IntelKey(owner_id=user.id, provider=IntelProvider(provider), secret_ref=ciphertext)
        )
    await audit.record(session, actor=user.id, action="intel.key_set", target=provider)
    await session.commit()
    return await _provider_out(session, user.id, provider)


@router.delete("/{provider}", status_code=204)
async def delete_key(
    provider: IntelProviderName,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await session.scalar(select(User).where(User.id == user.id).with_for_update())
    await session.execute(
        delete(IntelKey).where(
            IntelKey.owner_id == user.id,
            IntelKey.provider == IntelProvider(provider),
        )
    )
    await audit.record(session, actor=user.id, action="intel.key_delete", target=provider)
    await session.commit()
