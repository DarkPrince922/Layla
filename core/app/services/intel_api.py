"""Read-only adapters for the bundled MCP. Subjects are never fetched/resolved.

Destinations and methods are fixed; results cannot trigger more requests.
No scan-submission API is exposed. Errors never reflect upstream responses.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx

from app.schemas.intelligence import IntelArtifact, IntelResult
from app.services.intel_targets import is_ip, normalize_target

PROVIDERS = {
    "shodan": {
        "name": "Shodan",
        "docs_url": "https://developer.shodan.io/api",
        "supports_ip": True,
        "key_required": True,
    },
    "virustotal": {
        "name": "VirusTotal",
        "docs_url": "https://docs.virustotal.com/reference/overview",
        "supports_ip": True,
        "key_required": True,
    },
    "securitytrails": {
        "name": "SecurityTrails",
        "docs_url": "https://docs.securitytrails.com/reference",
        "supports_ip": False,
        "key_required": True,
    },
    "urlscan": {
        "name": "urlscan.io",
        "docs_url": "https://urlscan.io/docs/api/",
        "supports_ip": True,
        "key_required": False,
    },
}
ERRORS = {
    "not_configured": "Добавьте API-ключ в настройках интеграций.",
    "invalid_target": "Укажите корректный домен или публичный IP.",
    "unsupported_target": "Этот источник поддерживает поиск только по домену.",
    "authentication": "Провайдер отклонил ключ или тариф не даёт доступа к этому запросу.",
    "rate_limited": "Лимит запросов провайдера исчерпан. Повторите позже.",
    "timeout": "Источник не ответил вовремя. Повторите запрос позже.",
    "unavailable": "Источник недоступен. Повторите запрос позже.",
    "invalid_response": "Источник вернул некорректный или слишком большой ответ.",
    "mcp_error": "Не удалось выполнить запрос через MCP. Проверьте настройки сервиса.",
}
MAX_RESPONSE_BYTES = 512 * 1024


class IntelFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def failure(code: str) -> IntelResult:
    return IntelResult(status="error", error_code=code, error=ERRORS[code])


def _redact(value, secret: str):
    if isinstance(value, dict):
        return {
            _redact(k, secret): (
                "[redacted]"
                if k.lower()
                in {"key", "api_key", "apikey", "token", "access_token", "authorization"}
                else _redact(v, secret)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v, secret) for v in value]
    if isinstance(value, str) and secret:
        return value.replace(secret, "[redacted]")
    return value


async def _get_json(client: httpx.AsyncClient, url: str, *, headers: dict, params: dict):
    async with client.stream("GET", url, headers=headers, params=params) as response:
        if response.status_code == 404:
            return None
        if response.status_code in {401, 403}:
            raise IntelFailure("authentication")
        if response.status_code == 429:
            raise IntelFailure("rate_limited")
        if response.status_code != 200:
            # Includes redirects: never forward credentials to a new destination.
            raise IntelFailure("unavailable")
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > MAX_RESPONSE_BYTES:
                raise IntelFailure("invalid_response")
        try:
            data = json.loads(content)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise IntelFailure("invalid_response") from exc
        if not isinstance(data, dict):
            raise IntelFailure("invalid_response")
        return data


def _artifacts(provider: str, target: str, data: dict) -> list[IntelArtifact]:
    ip = is_ip(target)
    if provider == "urlscan":
        hits = data.get("results")
        if not isinstance(hits, list):
            raise IntelFailure("invalid_response")
        artifacts = []
        for hit in hits[:20]:
            if not isinstance(hit, dict):
                raise IntelFailure("invalid_response")
            try:
                scan_id = str(uuid.UUID(hit["_id"]))
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                raise IntelFailure("invalid_response") from exc
            page, task = hit.get("page") or {}, hit.get("task") or {}
            artifacts.append(
                IntelArtifact(
                    kind="public_scan",
                    title=str(page.get("title") or target)[:300],
                    summary=(
                        f"{page.get('url') or task.get('url') or target}\n"
                        f"IP: {page.get('ip') or '—'} · "
                        f"Сканирование источника: {task.get('time') or '—'}"
                    )[:8000],
                    source_url=f"https://urlscan.io/result/{scan_id}/",
                    data={k: hit[k] for k in ("_id", "page", "task", "stats") if k in hit},
                )
            )
        return artifacts
    if provider == "shodan":
        if ip:
            if "ip_str" not in data:
                raise IntelFailure("invalid_response")
            summary = f"Организация: {data.get('org') or '—'}. Порты: {data.get('ports') or []}."
            source, kind = f"https://www.shodan.io/host/{target}", "host"
        else:
            if not isinstance(data.get("data"), list):
                raise IntelFailure("invalid_response")
            if not data["data"] and not data.get("subdomains"):
                return []
            summary = (
                f"DNS-записей: {len(data['data'])}. "
                f"Поддоменов в ответе: {len(data.get('subdomains') or [])}. "
                f"Есть следующие страницы: {'да' if data.get('more') else 'нет'}."
            )
            source, kind = f"https://api.shodan.io/dns/domain/{target}", "dns"
    elif provider == "virustotal":
        attrs = data.get("data", {}).get("attributes")
        if not isinstance(attrs, dict):
            raise IntelFailure("invalid_response")
        stats = attrs.get("last_analysis_stats") or {}
        summary = (
            f"Вердикты источника: malicious {stats.get('malicious', 0)}, "
            f"suspicious {stats.get('suspicious', 0)}. "
            "Репутационный сигнал требует проверки контекста."
        )
        source = f"https://www.virustotal.com/gui/{'ip-address' if ip else 'domain'}/{target}"
        kind = "reputation"
    else:
        if "hostname" not in data:
            raise IntelFailure("invalid_response")
        summary = "Данные домена и DNS. Типы записей: " + ", ".join(
            sorted(data.get("current_dns") or {})
        )
        source, kind = f"https://securitytrails.com/domain/{target}/dns", "dns"
    return [
        IntelArtifact(
            kind=kind,
            title=f"{PROVIDERS[provider]['name']} · {target}"[:300],
            summary=summary[:8000],
            source_url=source,
            data=data,
        )
    ]


async def lookup(
    provider: str,
    target: str,
    api_key: str = "",
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> IntelResult:
    """One bounded query. transport is injectable for offline contract tests."""
    if provider not in PROVIDERS:
        return failure("mcp_error")
    try:
        target = normalize_target(target)
    except ValueError:
        return failure("invalid_target")
    if is_ip(target) and not PROVIDERS[provider]["supports_ip"]:
        return failure("unsupported_target")
    if not api_key and PROVIDERS[provider]["key_required"]:
        return failure("not_configured")
    headers = {"Accept": "application/json", "User-Agent": "Layla/0.1 (passive-intelligence)"}
    params: dict = {}
    if provider == "shodan":
        route = "shodan/host" if is_ip(target) else "dns/domain"
        url, params = f"https://api.shodan.io/{route}/{target}", {"key": api_key}
        if is_ip(target):
            params["minify"] = "true"
    elif provider == "virustotal":
        route = "ip_addresses" if is_ip(target) else "domains"
        url = f"https://www.virustotal.com/api/v3/{route}/{target}"
        headers["x-apikey"] = api_key
    elif provider == "securitytrails":
        url = f"https://api.securitytrails.com/v1/domain/{target}"
        headers["APIKEY"] = api_key
    else:
        url = "https://urlscan.io/api/v1/search/"
        params = {"q": f"{'ip' if is_ip(target) else 'domain'}:{target}", "size": "20"}
        if api_key:
            headers["api-key"] = api_key
    try:
        async with asyncio.timeout(20):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10, connect=5),
                follow_redirects=False,
                transport=transport,
                trust_env=False,
            ) as client:
                raw = await _get_json(client, url, headers=headers, params=params)
        if raw is None:
            return IntelResult(status="empty")
        artifacts = _artifacts(provider, target, _redact(raw, api_key))
        return IntelResult(status="ok" if artifacts else "empty", artifacts=artifacts)
    except IntelFailure as exc:
        return failure(exc.code)
    except (TimeoutError, httpx.TimeoutException):
        return failure("timeout")
    except httpx.HTTPError:
        return failure("unavailable")
    except (ValueError, TypeError, AttributeError, RecursionError):
        return failure("invalid_response")
