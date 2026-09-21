"""Клиент MCP на официальном Python SDK (спец. §5.8).

SDK импортируется лениво и опционально: реестр работает без него, а живая
проверка подключения (list tools) требует установленного пакета ``mcp`` и
запущенного/запускаемого сервера.
"""
from __future__ import annotations

import shlex


def sdk_available() -> bool:
    try:
        import mcp  # noqa: F401

        return True
    except Exception:
        return False


async def list_tools(*, transport: str, command: str | None, url: str | None,
                     env: dict | None = None, timeout: float = 20.0) -> list[dict]:
    """Подключиться к MCP-серверу и вернуть список инструментов.

    Бросает RuntimeError с понятным сообщением, если SDK недоступен или сервер
    не отвечает.
    """
    if not sdk_available():
        raise RuntimeError(
            "Python MCP SDK не установлен. Установите зависимость 'mcp', чтобы "
            "проверять подключение к серверам."
        )

    import asyncio

    async def _run() -> list[dict]:
        from mcp import ClientSession  # type: ignore

        if transport == "stdio":
            if not command:
                raise RuntimeError("Для stdio требуется команда запуска сервера")
            from mcp import StdioServerParameters  # type: ignore
            from mcp.client.stdio import stdio_client  # type: ignore

            parts = shlex.split(command)
            params = StdioServerParameters(command=parts[0], args=parts[1:], env=env or None)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    resp = await session.list_tools()
                    return [{"name": t.name, "description": t.description} for t in resp.tools]
        else:
            if not url:
                raise RuntimeError("Для http требуется URL сервера")
            from mcp.client.sse import sse_client  # type: ignore

            async with sse_client(url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    resp = await session.list_tools()
                    return [{"name": t.name, "description": t.description} for t in resp.tools]

    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Таймаут подключения к MCP-серверу") from exc
