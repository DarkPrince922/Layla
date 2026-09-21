"""Environment secrets for registered MCP workers, encrypted as one document."""

from __future__ import annotations

import json

from app.models.mcp import McpServer
from app.security import crypto


def encrypt_env(env: dict[str, str]) -> str | None:
    return crypto.encrypt(json.dumps(env)) if env else None


def decrypt_env(server: McpServer) -> dict[str, str]:
    if server.env_secret_ref is None:
        return {}
    return json.loads(crypto.decrypt(server.env_secret_ref))
