"""Official MCP SDK client for stdio, Streamable HTTP and legacy SSE."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


@asynccontextmanager
async def _session(
    *, transport: str, command: str | None, url: str | None, env: dict | None = None
):
    if transport == "stdio":
        parts = shlex.split(command or "")
        if not parts:
            raise ValueError("Missing stdio command")
        params = StdioServerParameters(command=parts[0], args=parts[1:], env=env or None)
        # External workers may print environment secrets. Do not forward their
        # raw stderr to the API's log sink.
        with open(os.devnull, "w") as errlog:  # noqa: ASYNC230 - local null device, no disk I/O
            async with (
                stdio_client(params, errlog=errlog) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                yield session
    elif transport == "http" and url:
        # Preserve old /sse registrations without retrying with another transport.
        if urlsplit(url).path.rstrip("/").endswith("/sse"):
            async with sse_client(url) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                yield session
        else:
            async with (
                streamable_http_client(url) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                yield session
    else:
        raise ValueError("Invalid MCP transport configuration")


async def list_tools(
    *,
    transport: str,
    command: str | None,
    url: str | None,
    env: dict | None = None,
    timeout: float = 20.0,
) -> list[dict]:
    try:
        async with asyncio.timeout(timeout):
            async with _session(transport=transport, command=command, url=url, env=env) as session:
                tools, seen, cursor = [], set(), None
                while True:
                    result = await session.list_tools(cursor=cursor)
                    tools.extend({"name": tool.name, "description": tool.description,
                                  "input_schema": getattr(tool, "inputSchema", None) or
                                                  {"type": "object", "additionalProperties": True}}
                                 for tool in result.tools)
                    if len(tools) >= 1000:
                        return tools[:1000]
                    cursor = result.nextCursor
                    if not cursor or cursor in seen:
                        return tools
                    seen.add(cursor)
    except TimeoutError as exc:
        raise RuntimeError("Таймаут подключения к MCP-серверу") from exc
    except Exception as exc:
        raise RuntimeError("Не удалось подключиться к MCP-серверу. Проверьте настройки.") from exc


async def call_tool(
    *,
    name: str,
    arguments: dict,
    transport: str,
    command: str | None = None,
    url: str | None = None,
    env: dict | None = None,
    timeout: float = 35.0,
    allow_text: bool = False,
) -> dict:
    try:
        async with asyncio.timeout(timeout):
            async with _session(transport=transport, command=command, url=url, env=env) as session:
                result = await session.call_tool(name, arguments=arguments)
                if result.isError:
                    raise ValueError("MCP tool failed")
                data = result.structuredContent
                if data is None:
                    text = "".join(c.text for c in result.content if c.type == "text")
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        if not allow_text:
                            raise
                        data = {"text": text[:60000], "truncated": len(text) > 60000}
                if not isinstance(data, dict):
                    if allow_text:
                        return {"result": data}
                    raise TypeError("Invalid tool result")
                return data
    except TimeoutError as exc:
        raise RuntimeError("Таймаут выполнения MCP-инструмента") from exc
    except Exception as exc:
        # Neither tool responses nor SDK errors may echo secrets to users.
        raise RuntimeError("Не удалось выполнить MCP-инструмент") from exc


async def list_resources(*, transport: str, command: str | None, url: str | None,
                         env: dict | None = None, cursor: str | None = None,
                         timeout: float = 20.0) -> dict:
    try:
        async with asyncio.timeout(timeout), _session(
            transport=transport, command=command, url=url, env=env,
        ) as session:
            result = await session.list_resources(cursor=cursor)
            return {"resources": [item.model_dump(mode="json", exclude_none=True)
                                  for item in result.resources[:100]],
                    "next_cursor": result.nextCursor}
    except Exception as exc:
        raise RuntimeError("Не удалось получить ресурсы MCP-сервера") from exc


async def read_resource(*, uri: str, transport: str, command: str | None, url: str | None,
                        env: dict | None = None, timeout: float = 20.0) -> dict:
    from pydantic import AnyUrl

    try:
        async with asyncio.timeout(timeout), _session(
            transport=transport, command=command, url=url, env=env,
        ) as session:
            result = await session.read_resource(AnyUrl(uri))
            contents, remaining = [], 60000
            for item in result.contents[:50]:
                text = getattr(item, "text", None)
                if text is None:
                    contents.append({"uri": str(item.uri), "mime_type": item.mimeType,
                                     "note": "Binary resource; use the server's file tools."})
                else:
                    contents.append({"uri": str(item.uri), "mime_type": item.mimeType,
                                     "text": text[:remaining], "truncated": len(text) > remaining})
                    remaining -= min(len(text), remaining)
                if remaining <= 0:
                    break
            return {"contents": contents}
    except Exception as exc:
        raise RuntimeError("Не удалось прочитать ресурс MCP-сервера") from exc


async def list_resource_templates(*, transport: str, command: str | None, url: str | None,
                                  env: dict | None = None, cursor: str | None = None,
                                  timeout: float = 20.0) -> dict:
    try:
        async with asyncio.timeout(timeout), _session(
            transport=transport, command=command, url=url, env=env,
        ) as session:
            result = await session.list_resource_templates(cursor=cursor)
            return {"templates": [item.model_dump(mode="json", exclude_none=True)
                                  for item in result.resourceTemplates[:100]],
                    "next_cursor": result.nextCursor}
    except Exception as exc:
        raise RuntimeError("Не удалось получить шаблоны ресурсов MCP-сервера") from exc
