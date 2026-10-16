"""Official MCP SDK client for stdio, Streamable HTTP and legacy SSE."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


@asynccontextmanager
async def _session(
    *, transport: str, command: str | None, url: str | None, env: dict | None = None,
    auth=None, message_handler=None,
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
                ClientSession(read, write, message_handler=message_handler) as session,
            ):
                await session.initialize()
                yield session
    elif transport == "http" and url:
        # Preserve old /sse registrations without retrying with another transport.
        if urlsplit(url).path.rstrip("/").endswith("/sse"):
            async with sse_client(url, auth=auth) as (read, write), ClientSession(read, write, message_handler=message_handler) as session:
                await session.initialize()
                yield session
        else:
            if auth is None and message_handler is None:
                async with streamable_http_client(url) as (read, write, _), ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
            else:
                async with (
                    httpx.AsyncClient(auth=auth, trust_env=False, follow_redirects=False,
                                      timeout=httpx.Timeout(connect=10, read=300, write=30, pool=10)) as client,
                    streamable_http_client(url, http_client=client) as (read, write, _),
                    ClientSession(read, write, message_handler=message_handler) as session,
                ):
                    await session.initialize()
                    yield session
    else:
        raise ValueError("Invalid MCP transport configuration")


class Connection:
    """One SDK task owns entry, requests and exit of its anyio cancel scopes.

    Do not replay a failed tool call: it may already have changed the server.
    A later request starts a fresh connection after transport failure/idle close.
    """
    def __init__(self, **params):
        self.params, self.notifications = params, []
        self.queue = asyncio.Queue(maxsize=32)
        self.task = None
        self.dirty = False
        self.in_flight = False

    async def notify(self, message):
        value = message.model_dump(mode="json") if hasattr(message, "model_dump") else {}
        if value:
            if "notifications/tools/list_changed" in json.dumps(value):
                self.dirty = True
            self.notifications.append(value)
            self.notifications[:] = self.notifications[-30:]

    async def _serve(self):
        active = None
        try:
            async with _session(**self.params, message_handler=self.notify) as session:
                while True:
                    method, kwargs, active = await asyncio.wait_for(self.queue.get(), 120)
                    if active.cancelled():
                        active = None
                        continue
                    self.in_flight = True
                    async with asyncio.timeout(35):
                        result = await getattr(session, method)(**kwargs)
                    if not active.done():
                        active.set_result(result)
                    active = None
                    self.in_flight = False
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001,S110 — do not leak MCP stderr, tokens or remote payloads
            pass
        finally:
            self.in_flight = False
            if active is not None and not active.done():
                active.set_exception(RuntimeError("MCP-соединение прервано; результат действия неизвестен. Не повторяйте изменение без проверки."))
            while not self.queue.empty():
                _, _, future = self.queue.get_nowait()
                if not future.done():
                    future.set_exception(RuntimeError("MCP-соединение закрыто; повторите запрос"))

    async def request(self, method, **kwargs):
        future = asyncio.get_running_loop().create_future()
        try:
            self.queue.put_nowait((method, kwargs, future))
        except asyncio.QueueFull as exc:
            raise RuntimeError("Очередь MCP заполнена; повторите запрос позже") from exc
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._serve(), name="code-mcp-connection")
        try:
            return await asyncio.wait_for(future, 45)
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None


def tool_result(result):
    if result.isError:
        raise RuntimeError("MCP-инструмент вернул ошибку")
    data = result.structuredContent
    if data is None:
        text = "".join(c.text for c in result.content if c.type == "text")
        try:
            data = json.loads(text)
        except ValueError:
            data = {"text": text[:60000], "truncated": len(text) > 60000}
    if not isinstance(data, dict):
        data = {"result": data}
    data = dict(data)
    data.pop("_code_binary", None)
    data.pop("code_attachments", None)
    binaries = []
    embedded = []
    for block in result.content[:20]:
        value = block.model_dump(mode="json", exclude_none=True)
        if block.type in ("image", "audio"):
            binaries.append({"data": value.get("data"), "mime_type": value.get("mimeType")})
        elif block.type == "resource":
            resource = value.get("resource", {})
            if "blob" in resource:
                binaries.append({"data": resource["blob"], "mime_type": resource.get("mimeType")})
            elif "text" in resource:
                embedded.append({"uri": resource.get("uri"), "text": resource["text"][:60000]})
    if binaries:
        data["_code_binary"] = binaries
    if embedded:
        data["mcp_resources"] = embedded
    return data


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
