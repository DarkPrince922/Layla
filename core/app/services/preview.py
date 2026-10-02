"""Шлюз превью приложения.

Превью открывается на отдельном адресе — другом порту Caddy, который помечает такие запросы
заголовком X-Layla-Preview. Любой путь на этом адресе принадлежит приложению пользователя и
уходит в песочницу; сама Лейла там недоступна. Вход — по ссылке с подписанным токеном
(/__layla__/open?t=…), дальше токен живёт в куке. Токен привязан к запуску превью: после
перезапуска старые ссылки перестают работать.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
from contextlib import suppress
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, quote

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.client import unix_connect as ws_unix_connect
from websockets.exceptions import WebSocketException

from app.config import get_settings
from app.security.jwt import COOKIE_NAME
from app.services import sandbox

logger = logging.getLogger("layla.preview")

COOKIE = "layla_preview"
MARK = b"x-layla-preview"
PICK_JS = (Path(__file__).resolve().parent.parent / "static" / "pick.js").read_bytes()
PICK_TAG = b'<script src="/__layla__/pick.js"></script>'
MAX_HTML = 5 * 1024 * 1024
_STATUS_TTL = 3.0
_HOP = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization", b"te", b"trailers",
        b"transfer-encoding", b"upgrade", b"host", b"content-length", b"x-layla-preview"}
_status: dict[tuple[str, str], tuple[float, dict]] = {}
_client: httpx.AsyncClient | None = None


# --- токен -----------------------------------------------------------------------

def _key() -> bytes:
    settings = get_settings()
    return hashlib.sha256(f"layla-preview:{settings.jwt_secret}:{settings.secret_key}".encode()).digest()


def _sign(owner_id: str, project_id: str, nonce: str) -> str:
    digest = hmac.new(_key(), f"{owner_id}|{project_id}|{nonce}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:18]).decode().rstrip("=")


def make_token(owner_id: str, project_id: str, nonce: str) -> str:
    return f"{project_id}.{owner_id}.{nonce}.{_sign(owner_id, project_id, nonce)}"


def parse_token(token: str | None) -> tuple[str, str, str] | None:
    """(владелец, проект, nonce) из подписанного токена или None."""
    parts = (token or "").split(".")
    if len(parts) != 4:
        return None
    project_id, owner_id, nonce, signature = parts
    if not hmac.compare_digest(signature, _sign(owner_id, project_id, nonce)):
        return None
    return owner_id, project_id, nonce


def base_url(scheme: str, host: str) -> str:
    """Адрес превью для браузера: явный LAYLA_PREVIEW_URL или тот же хост на порту превью."""
    settings = get_settings()
    if settings.preview_url.strip():
        return settings.preview_url.strip().rstrip("/")
    hostname = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if hostname.startswith("[") and "]" in hostname:
        hostname = hostname[: hostname.index("]") + 1]
    return f"{scheme}://{hostname}:{settings.preview_port}"


def open_url(base: str, token: str, path: str = "/") -> str:
    return f"{base}/__layla__/open?t={quote(token)}&path={quote(path or '/')}"


async def _live(owner_id: str, project_id: str, nonce: str) -> bool:
    """Превью этого запуска ещё работает (кешируется на пару секунд)."""
    key = (owner_id, project_id)
    cached = _status.get(key)
    if cached is None or time.monotonic() - cached[0] > _STATUS_TTL:
        try:
            info = await sandbox.preview_status(owner_id, project_id)
        except sandbox.SandboxError:
            info = {}
        cached = (time.monotonic(), info)
        _status[key] = cached
    info = cached[1]
    return info.get("nonce") == nonce and info.get("state") == "running"


# --- ASGI ---------------------------------------------------------------------------

def _headers(scope) -> dict[bytes, bytes]:
    return {k.lower(): v for k, v in scope.get("headers", [])}


def _cookie_token(headers: dict[bytes, bytes]) -> str | None:
    raw = headers.get(b"cookie", b"").decode("latin-1")
    if not raw:
        return None
    jar = SimpleCookie()
    with suppress(Exception):
        jar.load(raw)
    morsel = jar.get(COOKIE)
    return morsel.value if morsel else None


def _strip_cookies(raw: str) -> str:
    """Куки Лейлы приложению не нужны и не должны до него доходить."""
    kept = [part for part in raw.split(";") if part.strip().split("=", 1)[0] not in (COOKIE, COOKIE_NAME)]
    return ";".join(kept).strip()


async def _simple(send, status: int, body: bytes, content_type: bytes = b"text/html; charset=utf-8",
                  extra: list[tuple[bytes, bytes]] | None = None) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", content_type), (b"content-length", str(len(body)).encode()),
                            (b"cache-control", b"no-store"), *(extra or [])]})
    await send({"type": "http.response.body", "body": body})


def _page(title: str, text: str) -> bytes:
    return (f"<!doctype html><meta charset=utf-8><title>{title}</title>"
            "<body style='font:15px system-ui;background:#16131b;color:#ede4f2;display:grid;place-items:center;"
            f"height:100vh;margin:0'><div style='max-width:420px;text-align:center'><h2>{title}</h2>"
            f"<p style='color:#bcaec5'>{text}</p></div>").encode()


def _http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        socket_path = sandbox.sandbox_socket()
        transport = httpx.AsyncHTTPTransport(uds=socket_path) if socket_path else None
        _client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(300, connect=5))
    return _client


class PreviewGateway:
    """ASGI-обёртка: запросы с адреса превью уходят в приложение, остальные — в Лейлу."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket") and _headers(scope).get(MARK) == b"1":
            if scope["type"] == "http":
                await self._http(scope, receive, send)
            else:
                await self._ws(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _http(self, scope, receive, send) -> None:
        headers = _headers(scope)
        path = scope.get("path") or "/"
        if path == "/__layla__/pick.js":
            await _simple(send, 200, PICK_JS, b"text/javascript; charset=utf-8")
            return
        if path == "/__layla__/open":
            query = parse_qs(scope.get("query_string", b"").decode())
            token = (query.get("t") or [""])[0]
            target = (query.get("path") or ["/"])[0]
            if not target.startswith("/") or target.startswith("//"):
                target = "/"
            if parse_token(token) is None:
                await _simple(send, 403, _page("Ссылка на превью недействительна", "Откройте превью заново в Лейле."))
                return
            secure = headers.get(b"x-forwarded-proto", b"").split(b",")[0].strip() == b"https"
            cookie = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")
            await _simple(send, 302, b"", extra=[(b"location", target.encode()), (b"set-cookie", cookie.encode())])
            return
        parsed = parse_token(_cookie_token(headers))
        if parsed is None or not await _live(*parsed):
            await _simple(send, 503, _page("Превью не запущено",
                                           "Запустите его в Лейле: «Код» → «Превью». Если оно работает, откройте "
                                           "превью заново кнопкой в Лейле."))
            return
        owner_id, project_id, _ = parsed
        await self._forward(scope, receive, send, owner_id, project_id, headers)

    async def _forward(self, scope, receive, send, owner_id: str, project_id: str, headers) -> None:
        url = sandbox.sandbox_http_base() + sandbox.preview_path(owner_id, project_id, "http", scope.get("path", "/"))
        if scope.get("query_string"):
            url += "?" + scope["query_string"].decode("latin-1")
        out = []
        for key, value in scope.get("headers", []):
            name = key.lower()
            if name in _HOP or name.startswith(b"x-forwarded-"):
                continue
            if name == b"cookie":
                value = _strip_cookies(value.decode("latin-1")).encode("latin-1")
                if not value:
                    continue
            out.append((key.decode("latin-1"), value.decode("latin-1")))

        # Тело — только если оно есть: поток без длины ушёл бы как chunked, а строгие серверы
        # (и часть dev-серверов) GET с Transfer-Encoding отвергают.
        length = headers.get(b"content-length")
        has_body = b"transfer-encoding" in headers or (length is not None and length.strip() not in (b"", b"0"))
        if length is not None and has_body:
            out.append(("content-length", length.decode("latin-1").strip()))

        async def body():
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                if chunk:
                    yield chunk
                if not message.get("more_body"):
                    return

        client = _http_client()
        request = client.build_request(scope["method"], url, headers=out, content=body() if has_body else None)
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError as exc:
            await _simple(send, 502, _page("Превью не отвечает", f"Песочница недоступна: {type(exc).__name__}"))
            return
        try:
            kept = []
            for key, value in response.headers.raw:
                name = key.lower()
                if name in _HOP:
                    continue
                if name == b"set-cookie" and value.split(b"=", 1)[0].strip().decode("latin-1") in (COOKIE, COOKIE_NAME):
                    continue  # приложение не может подменить куки Лейлы
                kept.append((key, value))
            html = response.status_code == 200 and b"text/html" in response.headers.get("content-type", "").encode()
            if html:
                payload = b""
                async for chunk in response.aiter_raw():
                    payload += chunk
                    if len(payload) > MAX_HTML:
                        break
                payload = _inject(payload)
                kept.append((b"content-length", str(len(payload)).encode()))
                await send({"type": "http.response.start", "status": response.status_code, "headers": kept})
                await send({"type": "http.response.body", "body": payload})
                return
            length = response.headers.get("content-length")
            if length is not None:
                kept.append((b"content-length", length.encode()))
            await send({"type": "http.response.start", "status": response.status_code, "headers": kept})
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            await response.aclose()

    async def _ws(self, scope, receive, send) -> None:
        headers = _headers(scope)
        parsed = parse_token(_cookie_token(headers))
        first = await receive()
        if first["type"] != "websocket.connect":
            return
        if parsed is None or not await _live(*parsed):
            await send({"type": "websocket.close", "code": 1008})
            return
        owner_id, project_id, _ = parsed
        path = sandbox.preview_path(owner_id, project_id, "ws", scope.get("path", "/"))
        if scope.get("query_string"):
            path += "?" + scope["query_string"].decode("latin-1")
        protocols = [p.strip() for p in headers.get(b"sec-websocket-protocol", b"").decode().split(",") if p.strip()]
        socket_path = sandbox.sandbox_socket()
        try:
            if socket_path:
                upstream = await ws_unix_connect(socket_path, uri=f"ws://sandbox{path}", subprotocols=protocols or None,
                                                 max_size=None, open_timeout=10, ping_interval=None)
            else:
                base = sandbox.sandbox_http_base().replace("http", "ws", 1)
                upstream = await ws_connect(base + path, subprotocols=protocols or None, max_size=None,
                                            open_timeout=10, ping_interval=None)
        except (OSError, TimeoutError, WebSocketException):
            await send({"type": "websocket.close", "code": 1011})
            return
        accept: dict = {"type": "websocket.accept"}
        if upstream.subprotocol:
            accept["subprotocol"] = upstream.subprotocol
        await send(accept)

        async def down() -> None:
            async for message in upstream:
                if isinstance(message, bytes):
                    await send({"type": "websocket.send", "bytes": message})
                else:
                    await send({"type": "websocket.send", "text": message})

        async def up() -> None:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    await upstream.send(message["bytes"])
                elif message.get("text") is not None:
                    await upstream.send(message["text"])

        tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await upstream.close()
            with suppress(Exception):
                await send({"type": "websocket.close", "code": 1000})


def _inject(html: bytes) -> bytes:
    """Скрипт выбора элемента — в каждую HTML-страницу превью (в файлы проекта не пишется)."""
    lower = html.lower()
    at = lower.find(b"</head>")
    if at < 0:
        at = lower.find(b"</body>")
    return html[:at] + PICK_TAG + html[at:] if at >= 0 else html + PICK_TAG
