"""Code web_search via Tavily, using the owner's existing integration key.

POST /search: https://docs.tavily.com/documentation/api-reference/endpoint/search
Fixed Basic depth and no auto-parameters: reserve one credit before dispatch.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
from sqlalchemy import select

from app.models.enums import IntelProvider
from app.models.mcp import IntelKey
from app.security import crypto
from app.services import code_usage


async def _key(runtime):
    async with runtime.maker() as session:
        await runtime._authorize(session)
        row = await session.scalar(select(IntelKey).where(IntelKey.owner_id == runtime.owner_id,
            IntelKey.provider == IntelProvider.tavily).order_by(IntelKey.created_at.desc(), IntelKey.id).limit(1))
        if row is None:
            raise ValueError("Добавьте ключ в Настройки → Интеграции → Tavily")
        try:
            return crypto.decrypt(row.secret_ref)
        except ValueError as exc:
            raise ValueError("Сохраните ключ Tavily заново: не удалось расшифровать секрет") from exc


async def search(args):
    # Late import avoids a cycle: the archive runtime dispatches here.
    from app.services.code_web_http import url
    from app.services.code_web_tools import _domain_matches

    account = code_usage.ACTIVE.get()
    if account is None:
        raise ValueError("Поиск Tavily доступен в авторизованном Code-чате")
    secret = await _key(account.runtime)
    body = {"query": args.query, "search_depth": "basic", "topic": "general", "max_results": 8,
        "auto_parameters": False, "include_answer": False, "include_raw_content": False,
        "include_images": False, "include_usage": True}
    if args.allowed_domains:
        body.update(include_domains=args.allowed_domains, include_domains_mode="restrict")
    if args.blocked_domains:
        body["exclude_domains"] = args.blocked_domains
    started = time.monotonic()
    reservation = await account.reserve("tavily", "basic", [], [], {}, server_searches=1, external_search=True)
    usage, completed, rejected = None, False, False
    try:
        try:
            async with asyncio.timeout(65), httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(60, connect=10)) as client:  # noqa: SIM117
                async with client.stream("POST", "https://api.tavily.com/search",
                    headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"}, json=body) as response:
                    # Reject before parsing/echoing an upstream body, which may contain secrets.
                    status = response.status_code
                    if status != 200:
                        rejected = 400 <= status < 500
                        errors = {401: "неверный API-ключ", 403: "доступ запрещён", 429: "лимит частоты запросов",
                            432: "исчерпан лимит плана", 433: "исчерпан лимит pay-as-you-go"}
                        raise ValueError(f"Tavily: {errors.get(status, 'ошибка сервиса')} (HTTP {status})")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 2_000_000:
                            raise ValueError("Tavily: ответ превышает лимит размера")
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise ValueError("Tavily: некорректный JSON в ответе") from exc
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError("Tavily: некорректный формат результатов")  # noqa: TRY004
            credits = data.get("usage", {}).get("credits") if isinstance(data.get("usage"), dict) else None
            # Basic has a one-credit ceiling. Keep the reserve if usage is absent/invalid.
            if type(credits) is int and 0 <= credits <= 1:
                usage = {"input_tokens": 0, "output_tokens": 0, "web_search_requests": credits}
            hits, sources = [], []
            for hit in data["results"][:20]:
                if not isinstance(hit, dict):
                    continue
                try:
                    target = url(hit.get("url", ""))
                except (ValueError, TypeError, AttributeError):
                    continue
                if args.allowed_domains and not any(_domain_matches(target, value) for value in args.allowed_domains):
                    continue
                if any(_domain_matches(target, value) for value in args.blocked_domains):
                    continue
                source = {"title": str(hit.get("title") or "")[:500], "url": target}
                if source in sources:
                    continue
                sources.append(source)
                hits.append({**source, "content": str(hit.get("content") or "")[:4000]})
                if len(hits) == 8:
                    break
            completed = True
            return {"query": args.query, "results": [{"tool_use_id": "tavily", "content": hits}],
                "sources": sources, "durationSeconds": time.monotonic() - started, "backend": "tavily",
                "search_depth": "basic", "credits": credits if usage else None,
                "request_id": str(data.get("request_id") or "")[:200], "untrusted_content": True}
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            # No retries: the server may already have consumed a credit.
            raise ValueError("Tavily: сетевая ошибка или таймаут; запрос автоматически не повторён") from exc
    finally:
        await account.settle(reservation, usage, failed=not completed, rejected=rejected)
