"""Explicit browser OAuth for Code MCP: durable PKCE, issuer binding and refresh.

Metadata and token requests use pinned public HTTPS, no cookies/proxies or
redirects. Tokens never leave their selected MCP resource or enter API output.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import secrets
import socket
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

import httpx
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
)
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, or_, select, update

from app.models.code_mcp import CodeMcpAuth
from app.models.mcp import McpServer
from app.security import crypto


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str | None = Field(default=None, max_length=2000)
    client_secret: str | None = Field(default=None, max_length=8000)
    scope: str | None = Field(default=None, max_length=2000)


def encrypt(value):
    return crypto.encrypt(json.dumps(value))


def decrypt(value):
    return json.loads(crypto.decrypt(value)) if value else {}


def validate_url(url):
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 33 for c in url) or "\\" in url:
        raise ValueError("OAuth требует корректный публичный HTTPS URL")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("OAuth требует HTTPS без учётных данных и fragment")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and (not address.is_global or address.is_multicast):
        raise ValueError("OAuth endpoint не может использовать служебный адрес")
    return httpx.URL(url)


async def request(method, url, *, data=None, headers=None, probe=False):
    target = validate_url(url)
    addresses = await asyncio.get_running_loop().getaddrinfo(target.host, target.port or 443, type=socket.SOCK_STREAM)
    addresses = sorted({item[4][0] for item in addresses})
    if not addresses or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in addresses):
        raise ValueError("OAuth metadata/token endpoint не может использовать локальный или служебный адрес")
    host = target.host if target.port in (None, 443) else f"{target.host}:{target.port}"
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=12) as client:  # noqa: SIM117
        async with client.stream(method, target.copy_with(host=addresses[0]),
                headers={"Host": host, "Accept": "application/json", "Accept-Encoding": "identity", **(headers or {})},
                data=data if not isinstance(data, str) else None,
                content=data if isinstance(data, str) else None, extensions={"sni_hostname": target.host}) as response:
            if probe:
                return response.status_code, dict(response.headers), {}
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ValueError("Сжатые OAuth metadata не принимаются")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 65536:
                    raise ValueError("Ответ OAuth превышает 64 КиБ")
            try:
                document = json.loads(body)
            except (ValueError, UnicodeError):
                document = {}
            if not isinstance(document, dict):
                raise ValueError("OAuth endpoint должен вернуть JSON-объект")  # noqa: TRY004 — public validation
            return response.status_code, dict(response.headers), document


async def discover(resource):
    validate_url(resource)
    status, headers, _ = await request("GET", resource, probe=True)
    challenge = httpx.Response(status, headers=headers)
    protected = None
    for url in build_protected_resource_metadata_discovery_urls(extract_resource_metadata_from_www_auth(challenge), resource):
        code, _, document = await request("GET", url)
        if code == 200:
            protected = document
            break
    if not protected or not isinstance(protected.get("resource"), str) or not check_resource_allowed(resource, protected["resource"]):
        raise ValueError("MCP-сервер не опубликовал подходящую OAuth resource metadata")
    issuers = protected.get("authorization_servers")
    if not isinstance(issuers, list) or not issuers or not isinstance(issuers[0], str):
        raise ValueError("В metadata нет authorization_servers")
    issuer = issuers[0]
    validate_url(issuer)
    for url in build_oauth_authorization_server_metadata_discovery_urls(issuer, resource):
        code, _, meta = await request("GET", url)
        if code != 200:
            continue
        if meta.get("issuer") != issuer:
            raise ValueError("Issuer OAuth metadata не совпадает с выбранным authorization server")
        if "S256" not in meta.get("code_challenge_methods_supported", []):
            raise ValueError("OAuth-сервер должен поддерживать PKCE S256")
        validate_url(meta.get("authorization_endpoint"))
        validate_url(meta.get("token_endpoint"))
        scopes = extract_scope_from_www_auth(challenge)
        if scopes is None:
            scopes = " ".join(protected.get("scopes_supported") or [])
        return {**meta, "resource": protected["resource"], "scope": scopes}
    raise ValueError("Не найдена metadata сервера авторизации")


async def start(maker, server, args, callback, client_metadata_url):
    if str(getattr(server.transport, "value", server.transport)) != "http" or not server.enabled or not server.url:
        raise ValueError("OAuth доступен для включённых HTTP MCP-серверов")
    meta = await discover(server.url)
    client = {"client_id": args.client_id, "client_secret": args.client_secret,
              "token_endpoint_auth_method": ("client_secret_post" if "client_secret_post" in
                  meta.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])
                  else "client_secret_basic") if args.client_secret else "none"}
    if args.client_id is None:
        async with maker() as session:
            previous = await session.get(CodeMcpAuth, server.id)
            saved = decrypt(previous.config_secret) if previous and previous.owner_id == server.owner_id else {}
            if saved.get("server_url") == server.url and saved.get("issuer") == meta["issuer"] and saved.get("callback") == callback:
                client = saved["client"]
    if not client.get("client_id"):
        if meta.get("client_id_metadata_document_supported") and client_metadata_url.startswith("https://"):
            client["client_id"] = client_metadata_url
        elif meta.get("registration_endpoint"):
            registration = {"client_name": "Layla Code", "redirect_uris": [callback],
                "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                "token_endpoint_auth_method": "none"}
            code, _, client = await request("POST", meta["registration_endpoint"],
                headers={"Content-Type": "application/json"}, data=json.dumps(registration))
            if (code not in (200, 201) or not isinstance(client.get("client_id"), str)
                    or not 1 <= len(client["client_id"]) <= 2000 or client.get("token_endpoint_auth_method", "none") != "none"):
                raise ValueError("Регистрация OAuth-клиента не удалась; укажите client_id вручную")
        else:
            raise ValueError("Укажите зарегистрированный OAuth client_id")
    pkce, state = PKCEParameters.generate(), secrets.token_urlsafe(32)
    scope = args.scope if args.scope is not None else meta.get("scope", "")
    if not isinstance(scope, str) or len(scope) > 2000:
        raise ValueError("OAuth scope должен быть строкой до 2000 символов")
    config = {"server_url": server.url, "resource": meta["resource"], "issuer": meta["issuer"],
              "token_endpoint": meta["token_endpoint"], "revocation_endpoint": meta.get("revocation_endpoint"),
              "require_iss": bool(meta.get("authorization_response_iss_parameter_supported")),
              "client": client, "callback": callback}
    async with maker() as session:
        current = await session.get(McpServer, server.id)
        if current is None or current.owner_id != server.owner_id or current.url != server.url or not current.enabled:
            raise ValueError("Настройки MCP изменились; начните авторизацию заново")
        row = await session.get(CodeMcpAuth, server.id)
        if row is None:
            row = CodeMcpAuth(server_id=server.id, owner_id=server.owner_id, revision=0)
            session.add(row)
        row.config_secret, row.tokens_secret = encrypt(config), None
        row.pending_secret = encrypt({"verifier": pkce.code_verifier})
        row.state_hash = hashlib.sha256(state.encode()).hexdigest()
        row.expires_at, row.lease_until = datetime.now(UTC) + timedelta(minutes=10), None
        row.revision += 1
        await session.commit()
    query = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": callback,
        "state": state, "code_challenge": pkce.code_challenge, "code_challenge_method": "S256",
        "resource": meta["resource"]}
    if scope:
        query["scope"] = scope
    endpoint = meta["authorization_endpoint"]
    return {"authorization_url": endpoint + ("&" if "?" in endpoint else "?") + urlencode(query)}


def token_data(config, values):
    client = config["client"]
    return {**values, "client_id": client["client_id"], "resource": config["resource"],
            **({"client_secret": client["client_secret"]} if client.get("client_secret")
               and client.get("token_endpoint_auth_method") == "client_secret_post" else {})}


def token_headers(config):
    from urllib.parse import quote

    client = config["client"]
    if client.get("client_secret") and client.get("token_endpoint_auth_method") == "client_secret_basic":
        pair = quote(client["client_id"], safe="") + ":" + quote(client["client_secret"], safe="")
        return {"Authorization": "Basic " + base64.b64encode(pair.encode()).decode()}
    return {}


def tokens(document, previous=None):
    if (not isinstance(document.get("access_token"), str) or not document["access_token"]
            or len(document["access_token"]) > 16000 or str(document.get("token_type", "")).lower() != "bearer"):
        raise ValueError("OAuth-сервер не вернул корректный Bearer token")
    expires = document.get("expires_in")
    if expires is not None and (not isinstance(expires, (int, float)) or not 0 < expires <= 366 * 86400):
        raise ValueError("Некорректный срок действия OAuth token")
    if document.get("refresh_token") is not None and (not isinstance(document["refresh_token"], str)
                                                      or len(document["refresh_token"]) > 16000):
        raise ValueError("Некорректный OAuth refresh token")
    return {"access_token": document["access_token"],
        "refresh_token": document.get("refresh_token") or (previous or {}).get("refresh_token"),
        "expires_at": time.time() + expires if expires is not None else None}


async def callback(maker, owner_id, state, code, issuer=None, error=None):
    digest = hashlib.sha256(state.encode()).hexdigest()
    async with maker() as session:
        row = await session.scalar(select(CodeMcpAuth).where(CodeMcpAuth.state_hash == digest,
            CodeMcpAuth.owner_id == owner_id))
        if row is None or row.expires_at is None or row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
            raise ValueError("Авторизация истекла или state недействителен")
        config, pending, revision = decrypt(row.config_secret), decrypt(row.pending_secret), row.revision
        server = await session.get(McpServer, row.server_id)
        if server is None or not server.enabled or server.url != config["server_url"] or server.owner_id != owner_id:
            raise ValueError("MCP-сервер изменился или отключён")
        if (issuer is not None and issuer != config["issuer"]) or (config["require_iss"] and issuer is None):
            raise ValueError("Issuer OAuth callback не совпадает")
        claimed = await session.execute(update(CodeMcpAuth).where(CodeMcpAuth.server_id == row.server_id,
            CodeMcpAuth.revision == revision, CodeMcpAuth.state_hash == digest).values(
                state_hash=None, pending_secret=None, revision=revision + 1))
        await session.commit()
        if not claimed.rowcount or error or not code:
            raise ValueError("Авторизация отклонена или callback уже использован")
        response, _, document = await request("POST", config["token_endpoint"], headers=token_headers(config), data=token_data(config,
            {"grant_type": "authorization_code", "code": code, "redirect_uri": config["callback"],
             "code_verifier": pending["verifier"]}))
        if response != 200:
            raise ValueError("Обмен OAuth code не удался; начните авторизацию заново")
        result = await session.execute(update(CodeMcpAuth).where(CodeMcpAuth.server_id == row.server_id,
            CodeMcpAuth.revision == revision + 1).values(tokens_secret=encrypt(tokens(document)), expires_at=None,
                                                       revision=revision + 2))
        await session.commit()
        if not result.rowcount:
            raise ValueError("Авторизация была отключена или заменена")


async def access_token(maker, server, *, force=False):
    for _ in range(150):
        async with maker() as session:
            current = await session.get(McpServer, server.id)
            if current is None or not current.enabled or current.owner_id != server.owner_id or current.url != server.url:
                raise ValueError("MCP-сервер изменился или отключён")
            row = await session.get(CodeMcpAuth, server.id)
            if row is None:
                return None
            if row.owner_id != server.owner_id:
                raise ValueError("OAuth недоступен")
            config, previous = decrypt(row.config_secret), decrypt(row.tokens_secret)
            if config["server_url"] != server.url or not previous:
                raise ValueError("Нужна OAuth-авторизация MCP в настройках интеграций")
            expiry = previous.get("expires_at")
            if not force and (expiry is None or expiry > time.time() + 30):
                return previous["access_token"]
            if not previous.get("refresh_token"):
                raise ValueError("OAuth token истёк; подключите MCP заново")
            revision = row.revision
            claimed = await session.execute(update(CodeMcpAuth).execution_options(synchronize_session=False).where(CodeMcpAuth.server_id == server.id,
                CodeMcpAuth.revision == revision,
                or_(CodeMcpAuth.lease_until.is_(None), CodeMcpAuth.lease_until < datetime.now(UTC)),
            ).values(lease_until=datetime.now(UTC) + timedelta(seconds=25), revision=revision + 1))
            await session.commit()
            if claimed.rowcount:
                try:
                    status, _, document = await request("POST", config["token_endpoint"], headers=token_headers(config), data=token_data(config,
                        {"grant_type": "refresh_token", "refresh_token": previous["refresh_token"]}))
                    refreshed = encrypt(tokens(document, previous)) if status == 200 else None
                except Exception:  # noqa: BLE001 — sanitize remote responses, invalidate consumed refresh tokens
                    refreshed = None
                updated = await session.execute(update(CodeMcpAuth).where(CodeMcpAuth.server_id == server.id,
                    CodeMcpAuth.revision == revision + 1).values(tokens_secret=refreshed, lease_until=None,
                                                               revision=revision + 2))
                await session.commit()
                if not refreshed or not updated.rowcount:
                    raise ValueError("OAuth refresh не удался; подключите MCP заново")
                return decrypt(refreshed)["access_token"]
        force = False
        await asyncio.sleep(0.1)
    raise ValueError("OAuth refresh уже выполняется; повторите запрос")


class BearerAuth(httpx.Auth):
    requires_request_body = True
    def __init__(self, maker, server):
        self.maker, self.server = maker, server

    async def async_auth_flow(self, request):
        async with self.maker() as session:
            row = await session.get(CodeMcpAuth, self.server.id)
            resource = decrypt(row.config_secret)["resource"] if row else resource_url_from_server_url(self.server.url)
        if not check_resource_allowed(str(request.url), resource):
            raise ValueError("MCP token нельзя передавать другому ресурсу")
        token = await access_token(self.maker, self.server)
        if token:
            request.headers["Authorization"] = "Bearer " + token
        response = yield request
        if token and response.status_code == 401:
            refreshed = await access_token(self.maker, self.server, force=True)
            if not refreshed:
                raise ValueError("OAuth отключён")
            request.headers["Authorization"] = "Bearer " + refreshed
            yield request


async def revoke(maker, server):
    # Local revocation is unconditional and immediate, including pending callbacks.
    async with maker() as session:
        row = await session.get(CodeMcpAuth, server.id)
        config, saved = (decrypt(row.config_secret), decrypt(row.tokens_secret)) if row else ({}, {})
        await session.execute(delete(CodeMcpAuth).where(CodeMcpAuth.server_id == server.id,
                                                       CodeMcpAuth.owner_id == server.owner_id))
        await session.commit()
    if config.get("revocation_endpoint") and saved:
        try:
            await request("POST", config["revocation_endpoint"], headers=token_headers(config),
                data=token_data(config, {"token": saved.get("refresh_token") or saved["access_token"]}))
        except Exception:  # noqa: BLE001,S110 — local deletion already completed; never log token payloads
            pass
