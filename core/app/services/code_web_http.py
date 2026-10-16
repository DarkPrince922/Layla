"""Code port of WebFetchTool/utils.ts: HTTPS, redirects, preflight and raw cache.

Unlike the archive's axios transport, pin public DNS on every request. Credentials,
cookies and private addresses never cross from the model provider into this client.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from collections import OrderedDict
from importlib.resources import files
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

MAX_BYTES = 10 * 1024 * 1024
MAX_CACHE = 50 * 1024 * 1024
CACHE = OrderedDict()
DOMAIN_CACHE = OrderedDict()
PREAPPROVED = tuple(json.loads(files("app.services").joinpath("prompts/web_preapproved.json").read_text()))


def url(value):
    if len(value) > 2000 or any(ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("Некорректный URL WebFetch")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("WebFetch: нужен публичный HTTP(S) URL без credentials")
    if parsed.port not in (None, 80 if parsed.scheme == "http" else 443):
        raise ValueError("WebFetch: доступен только стандартный HTTPS-порт")
    host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    if ("." not in host and ":" not in host) or "%" in host:
        raise ValueError("WebFetch: нужен публичный домен или IP-адрес")
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global or address.is_multicast:
            raise ValueError("WebFetch: локальные и служебные адреса недоступны")
    except ValueError:
        if ":" in host or host.replace(".", "").isdigit():
            raise ValueError("WebFetch: некорректный или непубличный адрес") from None
    authority = "[" + host + "]" if ":" in host else host
    return urlunsplit(("https", authority, parsed.path or "/", parsed.query, ""))


def preapproved(value):
    parsed = urlsplit(url(value))
    return any(parsed.hostname == entry.split("/", 1)[0] and (
        "/" not in entry or parsed.path == "/" + entry.split("/", 1)[1]
        or parsed.path.startswith("/" + entry.split("/", 1)[1] + "/")) for entry in PREAPPROVED)


def permitted_redirect(original, target):
    a, b = urlsplit(original), urlsplit(target)
    return a.scheme == b.scheme and a.port == b.port and a.hostname.removeprefix("www.") == b.hostname.removeprefix("www.")


async def addresses(host):
    rows = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    values = sorted({row[4][0] for row in rows}, key=lambda v: (":" in v, v))
    if not values or any(not ipaddress.ip_address(v).is_global or ipaddress.ip_address(v).is_multicast for v in values):
        raise ValueError("WebFetch: DNS содержит локальный или служебный адрес")
    return values


async def get(value, *, limit=MAX_BYTES):
    value = url(value)
    target = httpx.URL(value)
    host = target.host
    pinned = target.copy_with(host=(await addresses(host))[0])
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(60, connect=10)) as client:  # noqa: SIM117
        async with client.stream("GET", pinned, headers={"Host": host,
            "User-Agent": "Layla-Code-WebFetch/1.0", "Accept": "text/markdown, text/html, */*",
            "Accept-Encoding": "identity"}, extensions={"sni_hostname": host}) as response:
            body = bytearray()
            async for part in response.aiter_bytes():
                body.extend(part)
                if len(body) > limit:
                    raise ValueError("WebFetch: ответ превышает лимит размера")
            if response.status_code == 403 and response.headers.get("x-proxy-error") == "blocked-by-allowlist":
                raise ValueError("WebFetch: доступ к домену закрыт сетевым proxy")
            return {"url": value, "code": response.status_code, "codeText": response.reason_phrase,
                "content_type": response.headers.get("content-type", ""), "body": bytes(body),
                "location": response.headers.get("location")}


async def check_domain(host):
    now = time.monotonic()
    if DOMAIN_CACHE.get(host, 0) > now:
        return
    from urllib.parse import urlencode
    async with asyncio.timeout(10):
        result = await get("https://api.anthropic.com/api/web/domain_info?" + urlencode({"domain": host}), limit=64000)
    try:
        data = json.loads(result["body"])
    except (ValueError, UnicodeError):
        data = {}
    if result["code"] != 200 or not isinstance(data, dict) or data.get("can_fetch") is not True:
        raise ValueError("WebFetch: домен запрещён или сервис проверки домена недоступен")
    DOMAIN_CACHE[host] = now + 300
    DOMAIN_CACHE.move_to_end(host)
    while len(DOMAIN_CACHE) > 128:
        DOMAIN_CACHE.popitem(last=False)


async def fetch(value, scope, *, skip_preflight=False):
    original = url(value)
    # Revalidate before cache lookup; a cached public response cannot bypass DNS
    # checks or a newly enabled preflight policy.
    async with asyncio.timeout(70):
        await addresses(urlsplit(original).hostname)
        if not skip_preflight:
            await check_domain(urlsplit(original).hostname)
        key = scope, original
        now = time.monotonic()
        for old in list(CACHE):
            if CACHE[old][0] <= now:
                del CACHE[old]
        if key in CACHE:
            CACHE.move_to_end(key)
            return {**CACHE[key][1], "cached": True}
        current = original
        for _hop in range(11):
            result = await get(current)
            if result["code"] in {301, 302, 303, 307, 308}:
                if not result["location"]:
                    raise ValueError("WebFetch: перенаправление без Location")
                target = url(urljoin(current, result["location"]))
                if not permitted_redirect(current, target):
                    return {"type": "redirect", "originalUrl": original, "redirectUrl": target,
                            "code": result["code"], "codeText": result["codeText"]}
                if not skip_preflight:
                    await check_domain(urlsplit(target).hostname)
                current = target
                continue
            if result["code"] != 200:
                raise ValueError("WebFetch: HTTP " + str(result["code"]))
            result.pop("location", None)
            CACHE[key] = now + 900, result
            while len(CACHE) > 512 or sum(len(item[1]["body"]) for item in CACHE.values()) > MAX_CACHE:
                CACHE.popitem(last=False)
            return dict(result)
    raise ValueError("WebFetch: больше десяти перенаправлений")
