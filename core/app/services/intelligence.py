"""Authenticated orchestration of the bundled MCP and encrypted intel keys."""

from __future__ import annotations

import shlex
import sys

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import IntelProvider
from app.models.mcp import IntelKey
from app.schemas.intelligence import IntelResult
from app.security import crypto
from app.services import intel_api, mcp_client

MCP_COMMAND = f"{shlex.quote(sys.executable)} -m app.mcp_servers.intelligence"


async def get_key(session: AsyncSession, owner_id: str, provider: str) -> IntelKey | None:
    return await session.scalar(
        select(IntelKey)
        .where(
            IntelKey.owner_id == owner_id,
            IntelKey.provider == IntelProvider(provider),
        )
        .order_by(IntelKey.created_at.desc(), IntelKey.id)
        .limit(1)
    )


async def run_lookup(
    session: AsyncSession, owner_id: str, provider: str, target: str
) -> IntelResult:
    row = await get_key(session, owner_id, provider)
    try:
        key = crypto.decrypt(row.secret_ref) if row else ""
    except ValueError:
        return intel_api.failure("not_configured")
    if not key and intel_api.PROVIDERS[provider]["key_required"]:
        return intel_api.failure("not_configured")
    try:
        result = await mcp_client.call_tool(
            name=f"{provider}_lookup",
            arguments={"target": target},
            transport="stdio",
            command=MCP_COMMAND,
            env={"LAYLA_INTEL_PROVIDER": provider, "LAYLA_INTEL_API_KEY": key},
        )
        parsed = IntelResult.model_validate(result)
        if parsed.status == "error":
            # Only our known, safe error vocabulary may enter storage/audit/UI.
            return intel_api.failure(
                parsed.error_code if parsed.error_code in intel_api.ERRORS else "mcp_error"
            )
        return parsed
    except (RuntimeError, ValidationError):
        return intel_api.failure("mcp_error")
