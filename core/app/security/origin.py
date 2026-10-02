"""Защита API от запросов с чужих страниц (CSRF).

Кука сессии Лейлы уходит браузером на любой порт того же хоста — в том числе со страниц
превью приложения, где работает код пользователя. Поэтому изменяющие запросы к /api
принимаются, только если их Origin совпадает с адресом, по которому открыта Лейла.
Запросы без Origin (curl, серверные клиенты, старые браузеры для GET) не затрагиваются.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

SAFE = {"GET", "HEAD", "OPTIONS"}


class OriginGuard:
    def __init__(self, app, extra: tuple[str, ...] = ()) -> None:
        self.app = app
        self.extra = {e.rstrip("/") for e in extra}

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope.get("method", "GET") not in SAFE and scope.get("path", "").startswith("/api"):
            headers = {k.lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            origin = headers.get(b"origin")
            if origin and origin != "null" and not self._same(origin, headers):
                body = json.dumps({"detail": "Запрос с чужой страницы отклонён"}, ensure_ascii=False).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
            if origin == "null":
                # Страница с непрозрачным источником (sandbox-iframe) не должна менять данные Лейлы.
                body = b'{"detail": "forbidden"}'
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)

    def _same(self, origin: str, headers: dict[bytes, str]) -> bool:
        if origin.rstrip("/") in self.extra:
            return True
        parsed = urlparse(origin)
        hosts = {headers.get(b"host", "")}
        hosts.update(h.strip() for h in headers.get(b"x-forwarded-host", "").split(",") if h.strip())
        netloc = parsed.netloc.lower()
        # Порт по умолчанию браузер в Origin не пишет, а в Host он может быть.
        default = {"http": ":80", "https": ":443"}.get(parsed.scheme, "")
        return any(h.lower() in (netloc, netloc + default) for h in hosts if h)
