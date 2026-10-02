"""Прокси выхода песочницы в интернет: только HTTPS (CONNECT) и только к разрешённым хостам.

Контейнер песочницы сидит во внутренней сети без выхода наружу; этот прокси — единственный
путь. По умолчанию пускает к реестрам пакетов (pip, npm, Go, Rust, Maven), чтобы
зависимости ставились, а код не мог отправить данные куда угодно. Адреса локальной сети,
loopback и метаданных облака закрыты всегда, даже при SANDBOX_ALLOW_HOSTS=*.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import os
import socket

log = logging.getLogger("layla.sandbox.proxy")

DEFAULT_HOSTS = (
    # Python
    "pypi.org", "files.pythonhosted.org",
    # JavaScript
    "registry.npmjs.org", "registry.yarnpkg.com", "repo.yarnpkg.com",
    # Go
    "proxy.golang.org", "sum.golang.org", "storage.googleapis.com",
    # Rust
    "crates.io", "index.crates.io", "static.crates.io", "static.rust-lang.org",
    # Java
    "repo.maven.apache.org", "repo1.maven.org", "plugins.gradle.org", "services.gradle.org",
)
PORTS = (443,)
MAX_HEAD = 16 * 1024


def allowed_hosts() -> tuple[str, ...]:
    raw = os.environ.get("SANDBOX_ALLOW_HOSTS", "").strip()
    extra = tuple(h.strip().lower().lstrip(".") for h in raw.split(",") if h.strip())
    if os.environ.get("SANDBOX_ALLOW_DEFAULTS", "true").lower() in ("0", "false", "no"):
        return extra
    return DEFAULT_HOSTS + extra


def host_allowed(host: str, hosts: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    return "*" in hosts or any(host == h or host.endswith("." + h) for h in hosts)


def public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast


async def _resolve(host: str, port: int) -> str:
    """Адрес хоста; только публичный. Подключаемся по нему же — без повторного DNS-запроса."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = [info[4][0] for info in infos]
    if not addresses or not all(public_ip(a) for a in addresses):
        raise PermissionError("адрес во внутренней сети")
    return addresses[0]


async def _reply(writer: asyncio.StreamWriter, status: str, text: str = "") -> None:
    body = text.encode()
    writer.write(f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                 f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
    try:
        await writer.drain()
    finally:
        writer.close()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, hosts: tuple[str, ...]) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, ConnectionError):
        writer.close()
        return
    line = head.split(b"\r\n", 1)[0].decode("latin-1")
    parts = line.split()
    if len(parts) != 3 or parts[0].upper() != "CONNECT":
        await _reply(writer, "405 Method Not Allowed",
                     "Layla sandbox proxy: only HTTPS (CONNECT) is allowed.\n")
        return
    host, _, port_text = parts[1].rpartition(":")
    host = host.strip("[]")
    try:
        port = int(port_text)
    except ValueError:
        await _reply(writer, "400 Bad Request")
        return
    if port not in PORTS or not host_allowed(host, hosts):
        log.warning("denied %s:%s", host, port)
        await _reply(writer, "403 Forbidden",
                     f"Layla sandbox: {host} is not in the allowed hosts (SANDBOX_ALLOW_HOSTS).\n")
        return
    try:
        address = await _resolve(host, port)
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(address, port), 20)
    except PermissionError:
        log.warning("denied private address for %s", host)
        await _reply(writer, "403 Forbidden", f"Layla sandbox: {host} resolves to a private address.\n")
        return
    except (OSError, TimeoutError):
        await _reply(writer, "502 Bad Gateway", f"Layla sandbox: cannot connect to {host}.\n")
        return
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))


async def serve(port: int = 3128) -> None:
    hosts = allowed_hosts()
    server = await asyncio.start_server(lambda r, w: handle(r, w, hosts), "0.0.0.0", port, limit=MAX_HEAD)
    log.warning("sandbox proxy on :%s, allowed: %s", port, "*" if "*" in hosts else ", ".join(hosts))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(serve(int(os.environ.get("SANDBOX_PROXY_PORT", "3128"))))
