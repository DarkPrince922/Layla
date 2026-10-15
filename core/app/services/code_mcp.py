"""Expose the user's enabled MCP servers to the Code agent."""
from __future__ import annotations

import re

from app.models.mcp import McpServer
from app.services import mcp_client
from app.services.mcp_secrets import decrypt_env


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")[:40] or "server"


class McpAgent:
    def __init__(self, routes: dict[str, tuple[McpServer, str]], specs: list[dict]) -> None:
        self.routes = routes
        self.specs = specs

    @classmethod
    async def create(cls, servers: list[McpServer]) -> McpAgent | None:
        routes: dict[str, tuple[McpServer, str]] = {}
        specs: list[dict] = []
        for server in servers:
            try:
                remote = await mcp_client.list_tools(
                    transport=server.transport.value if hasattr(server.transport, "value") else str(server.transport),
                    command=server.command, url=server.url, env=decrypt_env(server), timeout=8,
                )
            except (RuntimeError, ValueError):
                continue
            prefix = _slug(server.name)
            for tool in remote[:100]:
                original = str(tool.get("name") or "")
                if not original:
                    continue
                wire = f"mcp__{prefix}__{_slug(original)}"[:120]
                if wire in routes:
                    continue
                schema = tool.get("input_schema")
                if not isinstance(schema, dict) or schema.get("type") not in (None, "object"):
                    schema = {"type": "object", "additionalProperties": True}
                routes[wire] = (server, original)
                specs.append({"type": "function", "function": {
                    "name": wire,
                    "description": f"MCP {server.name}: {str(tool.get('description') or original)[:2000]}",
                    "parameters": schema,
                }})
        return cls(routes, specs) if routes else None

    async def call(self, name: str, arguments: dict) -> dict:
        server, original = self.routes[name]
        return await mcp_client.call_tool(
            name=original, arguments=arguments,
            transport=server.transport.value if hasattr(server.transport, "value") else str(server.transport),
            command=server.command, url=server.url, env=decrypt_env(server),
        )

    def label(self, name: str) -> str:
        server, original = self.routes[name]
        return f"{server.name} · {original}"
