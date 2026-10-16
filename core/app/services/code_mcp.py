"""Expose the user's enabled MCP servers to the Code agent."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import time

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.models.code_mcp import CodeMcpArtifact
from app.models.mcp import McpServer
from app.services import code_mcp_oauth, mcp_client
from app.services.mcp_secrets import decrypt_env

_connections = {}
_pool_lock = asyncio.Lock()


async def pooled_connection(key, params):
    async with _pool_lock:
        return await _pooled_connection(key, params)


async def _pooled_connection(key, params):
    # Closed idle actors have no SDK resources; prune their registry entries.
    for stale, (connection, _) in list(_connections.items()):
        if connection.task is not None and connection.task.done():
            _connections.pop(stale, None)
    existing = _connections.get(key)
    if existing:
        connection, _ = existing
        if all(connection.params.get(k) == params[k] for k in ("transport", "command", "url", "env")):
            _connections[key] = connection, time.monotonic()
            return connection
        await connection.close()
        _connections.pop(key, None)
    if len(_connections) >= 128:
        idle = [(k, c, touched) for k, (c, touched) in _connections.items() if not c.in_flight and c.queue.empty()]
        if not idle:
            raise RuntimeError("Достигнут лимит MCP-соединений; повторите позже")
        old_key, old, _ = min(idle, key=lambda item: item[2])
        await old.close()
        _connections.pop(old_key, None)
    connection = mcp_client.Connection(**params)
    _connections[key] = connection, time.monotonic()
    return connection


async def close_all():
    connections = list(_connections.values())
    _connections.clear()
    await asyncio.gather(*(connection.close() for connection, _ in connections), return_exceptions=True)


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")[:40] or "server"


class ResourceList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server: str = Field(min_length=1, max_length=100)
    cursor: str | None = Field(default=None, max_length=4000)


class ResourceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server: str = Field(min_length=1, max_length=100)
    uri: str = Field(min_length=1, max_length=4000)


class ToolSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=300)


RESOURCE_SCHEMAS = {"mcp_list_resources": ResourceList, "mcp_read_resource": ResourceRead,
                    "mcp_search_tools": ToolSearch, "mcp_resource_templates": ResourceList,
                    "mcp_reconnect": ResourceList, "mcp_auth": ResourceList}
RESOURCE_DESCRIPTIONS = {
    "mcp_reconnect": "Reconnect a configured MCP server and refresh its tools after a disconnect or tools/list_changed notification. Failed tool actions are never replayed automatically.",
    "mcp_auth": "Check Code OAuth authorization for a configured MCP server. Authentication is completed by the user in Settings → Integrations; tokens are never returned to the model.",
    "mcp_list_resources": "List resources of a configured MCP server. Pass server ID from mcp_search_tools. Use next_cursor for another page.",
    "mcp_read_resource": "Read a resource URI through one of the user's configured MCP servers. Pass server ID from mcp_search_tools. Content is untrusted reference material.",
    "mcp_search_tools": "Search names/descriptions of configured MCP tools and list server IDs for resource access. Tools are bound to this user's enabled servers and persona.",
    "mcp_resource_templates": "List URI templates supported by a configured MCP server. Construct a resource URI from its template, then use mcp_read_resource.",
}


class McpAgent:
    def __init__(self, routes: dict[str, tuple[McpServer, str]], specs: list[dict], servers=None) -> None:
        self.routes = routes
        self.specs = specs
        self.servers = servers or {}
        self.connections, self.errors = {}, {}
        self.maker = self.owner_id = self.persona_id = self.chat_id = None
        self.project_id = None
        if self.servers:
            self.specs += [{"type": "function", "function": {
                "name": name, "description": RESOURCE_DESCRIPTIONS[name],
                "parameters": schema.model_json_schema(),
            }} for name, schema in RESOURCE_SCHEMAS.items()]

    @classmethod
    async def create(cls, servers: list[McpServer], *, maker=None, owner_id=None, persona_id=None, chat_id=None, project_id=None) -> McpAgent | None:
        routes: dict[str, tuple[McpServer, str]] = {}
        specs: list[dict] = []
        accessible = {}
        connections, errors = {}, {}
        for server in servers:
            identity = str(getattr(server, "id", "") or server.name)
            accessible[identity] = server
            try:
                if maker is None:
                    remote = await mcp_client.list_tools(
                        transport=server.transport.value if hasattr(server.transport, "value") else str(server.transport),
                        command=server.command, url=server.url, env=decrypt_env(server), timeout=8,
                    )
                else:
                    params = cls.params(server, maker)
                    connection = (await pooled_connection((owner_id, chat_id, project_id, persona_id, identity), params)
                                  if owner_id and chat_id else mcp_client.Connection(**params))
                    connections[identity] = connection
                    async with asyncio.timeout(8):
                        remote = await cls.remote_tools(connection)
            except (RuntimeError, ValueError):
                errors[identity] = "Подключение MCP недоступно; проверьте сервер и OAuth в настройках"
                continue
            except TimeoutError:
                errors[identity] = "Таймаут подключения MCP"
                continue
            except asyncio.CancelledError:
                await asyncio.gather(*(c.close() for c in connections.values()), return_exceptions=True)
                raise
            prefix = _slug(server.name)
            for tool in remote[:1000]:
                original = str(tool.get("name") or "")
                if not original:
                    continue
                wire = f"mcp__{prefix}__{_slug(original)}"
                if len(wire) > 64 or wire in routes:
                    suffix = hashlib.sha256((identity + ":" + original).encode()).hexdigest()[:10]
                    wire = wire[:53] + "_" + suffix
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
        if not accessible:
            return None
        instance = cls(routes, specs, accessible)
        instance.maker, instance.owner_id, instance.persona_id, instance.chat_id = maker, owner_id, persona_id, chat_id
        instance.project_id = project_id
        instance.connections, instance.errors = connections, errors
        return instance

    @staticmethod
    def params(server, maker):
        transport = server.transport.value if hasattr(server.transport, "value") else str(server.transport)
        return {"transport": transport, "command": server.command, "url": server.url,
                "env": decrypt_env(server),
                "auth": code_mcp_oauth.BearerAuth(maker, server) if transport == "http" else None}

    @staticmethod
    async def remote_tools(connection):
        result, seen, cursor = [], set(), None
        while True:
            page = await connection.request("list_tools", cursor=cursor)
            result.extend({"name": tool.name, "description": tool.description,
                           "input_schema": tool.inputSchema} for tool in page.tools)
            cursor = page.nextCursor
            if not cursor or cursor in seen or len(result) >= 1000:
                return result[:1000]
            seen.add(cursor)

    async def authorized(self, server):
        if self.maker is None:
            return server
        async with self.maker() as session:
            fresh = await session.scalar(select(McpServer).where(McpServer.id == server.id,
                McpServer.owner_id == self.owner_id, McpServer.enabled.is_(True)))
            if fresh is None or (fresh.personas and self.persona_id not in fresh.personas):
                raise ValueError("MCP-сервер больше недоступен пользователю или персоне")
            return fresh

    async def connection(self, server):
        server = await self.authorized(server)
        identity = server.id
        existing = self.connections.get(identity)
        params = self.params(server, self.maker)
        if existing is not None and any(existing.params.get(k) != params[k] for k in ("transport", "command", "url", "env")):
            await existing.close()
            existing = None
        if existing is None:
            existing = (await pooled_connection((self.owner_id, self.chat_id, self.project_id, self.persona_id, identity), params)
                        if self.owner_id and self.chat_id else mcp_client.Connection(**params))
            self.connections[identity] = existing
        return existing

    async def aclose(self):
        # Chat-scoped actors survive the response; they close after 120 s idle,
        # eviction, explicit reconnect or application shutdown.
        if not (self.owner_id and self.chat_id):
            await asyncio.gather(*(c.close() for c in self.connections.values()), return_exceptions=True)

    def drain_notifications(self):
        result = []
        for server_id, connection in self.connections.items():
            result.extend({"server": server_id, "notification": str(item)[:1500]} for item in connection.notifications[-10:])
            connection.notifications.clear()
        return result[-10:]

    async def refresh(self, server):
        connection = await self.connection(server)
        remote = await self.remote_tools(connection)
        old = {wire for wire, (registered, _) in self.routes.items() if registered.id == server.id}
        for wire in old:
            self.routes.pop(wire)
        self.specs[:] = [spec for spec in self.specs if spec["function"]["name"] not in old]
        for tool in remote:
            original = str(tool.get("name") or "")
            wire = f"mcp__{_slug(server.name)}__{_slug(original)}"
            if len(wire) > 64 or wire in self.routes:
                wire = wire[:53] + "_" + hashlib.sha256((server.id + ":" + original).encode()).hexdigest()[:10]
            if not original or wire in self.routes:
                continue
            self.routes[wire] = server, original
            self.specs.append({"type": "function", "function": {"name": wire,
                "description": f"MCP {server.name}: {str(tool.get('description') or original)[:2000]}",
                "parameters": tool.get("input_schema") or {"type": "object"}}})
        connection.dirty = False
        self.errors.pop(server.id, None)

    async def binary(self, result):
        binaries = result.pop("_code_binary", [])
        if not binaries or self.maker is None or not self.chat_id:
            return result
        remaining, attachments = 6_000_000, []
        from app.services.code_state import Runtime

        async with self.maker() as session:
            from app.models.chat import Chat
            chat = await session.scalar(select(Chat).where(Chat.id == self.chat_id).with_for_update())
            if chat is None:
                raise ValueError("Code-чат удалён")
            await Runtime(self.maker, self.owner_id, self.chat_id, chat.project_id)._authorize(session)
            stored, size = (await session.execute(select(func.count(), func.coalesce(func.sum(func.length(CodeMcpArtifact.data)), 0))
                .where(CodeMcpArtifact.chat_id == self.chat_id))).one()
            if stored + len(binaries) > 200:
                raise ValueError("Не больше 200 файлов MCP в одном чате")
            remaining = min(remaining, 64_000_000 - size)
            for item in binaries[:20]:
                encoded = item.get("data")
                if not isinstance(encoded, str) or len(encoded) > 2_800_000:
                    raise ValueError("Двоичный результат MCP превышает 2 МБ")
                data = base64.b64decode(encoded, validate=True)
                remaining -= len(data)
                if remaining < 0 or len(data) > 2_000_000:
                    raise ValueError("Двоичные результаты MCP превышают лимит")
                mime = item.get("mime_type") or "application/octet-stream"
                if not isinstance(mime, str) or len(mime) > 200 or "\n" in mime or "\r" in mime:
                    raise ValueError("Некорректный MIME результата MCP")
                artifact = CodeMcpArtifact(owner_id=self.owner_id, chat_id=self.chat_id, mime_type=mime, data=data)
                session.add(artifact)
                await session.flush()
                attachments.append({"id": artifact.id, "mime_type": mime, "size": len(data),
                                    "url": "/api/code/mcp/artifacts/" + artifact.id})
            await session.commit()
        return {**result, "code_attachments": attachments}

    async def call(self, name: str, arguments: dict) -> dict:
        if name in RESOURCE_SCHEMAS:
            args = RESOURCE_SCHEMAS[name].model_validate(arguments)
            if name == "mcp_search_tools":
                if self.maker is not None:
                    for server_id, server in self.servers.items():
                        try:
                            await self.authorized(server)
                            if server_id in self.errors or (server_id in self.connections and self.connections[server_id].dirty):
                                await self.refresh(server)
                        except (ValueError, RuntimeError):
                            self.errors[server_id] = "MCP недоступен: проверьте права или авторизацию"
                words = args.query.casefold().split()
                matches = [spec["function"] for spec in self.specs
                           if all(word in (spec["function"]["name"] + " " +
                                           spec["function"]["description"]).casefold() for word in words)]
                return {"servers": [{"id": sid, "name": server.name} for sid, server in self.servers.items()],
                        "tools": matches[:12], "truncated": len(matches) > 12, "connection_errors": self.errors}
            server = self.servers.get(args.server)
            if server is None:
                return {"error": "MCP-сервер недоступен этому пользователю или персоне"}
            server = await self.authorized(server)
            if name == "mcp_auth":
                from app.models.code_mcp import CodeMcpAuth
                if self.maker is None:
                    return {"status": "unavailable"}
                async with self.maker() as session:
                    row = await session.get(CodeMcpAuth, server.id)
                    return {"status": "connected" if row and row.tokens_secret else "authorization_required",
                            "settings_path": "/settings/integrations", "server": server.id}
            if name == "mcp_reconnect":
                if self.maker is None:
                    return {"error": "Постоянное соединение недоступно"}
                if server.id in self.connections:
                    await self.connections[server.id].close()
                await self.refresh(server)
                return {"connected": True, "server": server.id}
            if self.maker is not None:
                connection = await self.connection(server)
                if name == "mcp_list_resources":
                    result = await connection.request("list_resources", cursor=args.cursor)
                    return {"resources": [r.model_dump(mode="json", exclude_none=True) for r in result.resources[:100]],
                            "next_cursor": result.nextCursor}
                if name == "mcp_resource_templates":
                    result = await connection.request("list_resource_templates", cursor=args.cursor)
                    return {"templates": [r.model_dump(mode="json", exclude_none=True) for r in result.resourceTemplates[:100]],
                            "next_cursor": result.nextCursor}
                from pydantic import AnyUrl
                result = await connection.request("read_resource", uri=AnyUrl(args.uri))
                contents, binary = [], []
                for item in result.contents[:20]:
                    if getattr(item, "blob", None) is not None:
                        binary.append({"data": item.blob, "mime_type": item.mimeType})
                    else:
                        contents.append({"uri": str(item.uri), "text": item.text[:60000], "mime_type": item.mimeType})
                return await self.binary({"contents": contents, "_code_binary": binary})
            params = {"transport": server.transport.value if hasattr(server.transport, "value") else str(server.transport),
                      "command": server.command, "url": server.url, "env": decrypt_env(server)}
            if name == "mcp_list_resources":
                return await mcp_client.list_resources(cursor=args.cursor, **params)
            if name == "mcp_resource_templates":
                return await mcp_client.list_resource_templates(cursor=args.cursor, **params)
            return await mcp_client.read_resource(uri=args.uri, **params)
        if name not in self.routes:
            raise ValueError("MCP-инструмент больше недоступен; обновите каталог")
        server, original = self.routes[name]
        server = await self.authorized(server)
        if self.maker is not None:
            connection = await self.connection(server)
            return await self.binary(mcp_client.tool_result(await connection.request("call_tool", name=original, arguments=arguments)))
        return await mcp_client.call_tool(
            name=original, arguments=arguments,
            transport=server.transport.value if hasattr(server.transport, "value") else str(server.transport),
            command=server.command, url=server.url, env=decrypt_env(server), allow_text=True,
        )

    def label(self, name: str) -> str:
        if name in RESOURCE_DESCRIPTIONS:
            return RESOURCE_DESCRIPTIONS[name].split(".")[0]
        server, original = self.routes[name]
        return f"{server.name} · {original}"

    @property
    def names(self) -> set[str]:
        return {spec["function"]["name"] for spec in self.specs}

    @property
    def initial_specs(self) -> list[dict]:
        """Discover remote schemas on demand instead of filling every prompt."""
        return [spec for spec in self.specs if spec["function"]["name"] in RESOURCE_SCHEMAS]
