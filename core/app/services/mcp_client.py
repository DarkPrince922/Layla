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
                result = await session.list_tools()
                return [{"name": t.name, "description": t.description} for t in result.tools]
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
                    data = json.loads(text)
                if not isinstance(data, dict):
                    raise TypeError("Invalid tool result")
                return data
    except TimeoutError as exc:
        raise RuntimeError("Таймаут выполнения MCP-инструмента") from exc
    except Exception as exc:
        # Neither tool responses nor SDK errors may echo secrets to users.
        raise RuntimeError("Не удалось выполнить MCP-инструмент") from exc
