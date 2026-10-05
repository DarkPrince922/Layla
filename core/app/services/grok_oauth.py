"""xAI OAuth device grant and renewable subscription credentials.

Protocol: xai-org/grok-build, xai-grok-login/{config,device_code}.rs.
Only xAI's fixed HTTPS endpoints receive device codes and refresh tokens.
"""
import json
import base64
import os
import time
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from app.models.provider import Provider
from app.security import crypto
from app.services.provider_errors import ProviderError

ISSUER = "https://auth.x.ai"
BASE_URL = "https://cli-chat-proxy.grok.com/v1"
# Public device client used by the open-source Grok CLI; deployments may register their own.
CLIENT_ID = os.getenv("LAYLA_GROK_CLIENT_ID", "b1a00492-073a-47ea-816f-4c329264a828")
SCOPES = "openid profile email offline_access grok-cli:access api:access"
TOKEN_PREFIX = "grok-oauth:"


async def request_device():
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(ISSUER + "/oauth2/device/code", data={
            "client_id": CLIENT_ID, "scope": SCOPES, "referrer": "layla",
        }, headers={"x-grok-client-surface": "ui"})
        response.raise_for_status()
        data = response.json()
    for field in ("verification_uri", "verification_uri_complete"):
        if data.get(field):
            url = urlsplit(data[field])
            if url.scheme != "https" or url.hostname not in ("auth.x.ai", "accounts.x.ai") or url.username or url.password:
                raise ValueError("Invalid verification URL")
    if not all(isinstance(data.get(k), str) and data[k] for k in ("device_code", "user_code", "verification_uri")):
        raise ValueError("Invalid device response")
    data["expires_in"] = min(max(int(data["expires_in"]), 1), 1800)
    data["interval"] = min(max(int(data.get("interval", 5)), 5), 60)
    return data


async def exchange(data):
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(ISSUER + "/oauth2/token", data={"client_id": CLIENT_ID, **data},
                                     headers={"x-grok-client-surface": "ui"})
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("Invalid token response")
    if response.is_error and result.get("error") in ("authorization_pending", "slow_down", "access_denied", "expired_token", "invalid_grant"):
        return result
    response.raise_for_status()
    if not isinstance(result.get("access_token"), str) or not result["access_token"]:
        raise ValueError("Missing access token")
    if not isinstance(result.get("token_type", "Bearer"), str) or result.get("token_type", "Bearer").lower() != "bearer":
        raise ValueError("Unsupported token type")
    if int(result.get("expires_in", 900)) <= 0:
        raise ValueError("Invalid token expiry")
    if result.get("refresh_token") is not None and not isinstance(result["refresh_token"], str):
        raise ValueError("Invalid refresh token")
    return result


def credentials(tokens, previous=None):
    previous = previous or {}
    user_id = previous.get("user_id", "")
    if tokens.get("id_token"):
        try:
            raw = tokens["id_token"].split(".")[1]
            # Only a metadata hint: xAI validates the access token, never this decoded value.
            claims = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            user_id = str(claims.get("sub") or user_id)
        except (ValueError, IndexError, TypeError):
            pass
    return {"grok_oauth": True, "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token") or previous.get("refresh_token"),
            "user_id": user_id,
            "client_id": previous.get("client_id") or CLIENT_ID,
            "expires_at": time.time() + max(1, int(tokens.get("expires_in", 900)))}


def wire_key(data):
    return TOKEN_PREFIX + json.dumps({"access_token": data["access_token"], "user_id": data.get("user_id", "")})


def wire_credentials(key):
    raw = key.removeprefix(TOKEN_PREFIX)
    return json.loads(raw) if raw.startswith("{") else {"access_token": raw}


def read_credentials(secret):
    if not secret:
        return None
    try:
        plain = crypto.decrypt(secret)
    except ValueError:
        return None
    if not plain.startswith('{'):
        return None
    data = json.loads(plain)
    return data if isinstance(data, dict) and data.get("grok_oauth") is True else None


async def access_key(session, provider):
    data = read_credentials(provider.secret_ref)
    if not data:
        return None
    if provider.base_url != BASE_URL:
        raise ProviderError("Некорректный адрес подключённого аккаунта Grok")
    if data["expires_at"] <= time.time() + 60:
        # DB lock coordinates token rotation between web workers and parallel agents.
        locked = await session.scalar(select(Provider).where(Provider.id == provider.id)
                                      .with_for_update().execution_options(populate_existing=True))
        data = read_credentials(locked.secret_ref)
        if data["expires_at"] <= time.time() + 60:
            if not data.get("refresh_token"):
                raise ProviderError("Вход Grok истёк. Подключите аккаунт снова.")
            try:
                tokens = await exchange({"grant_type": "refresh_token", "refresh_token": data["refresh_token"],
                                         "client_id": data.get("client_id", CLIENT_ID)})
                if tokens.get("error"):
                    raise ValueError("Refresh rejected")
                data = credentials(tokens, data)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise ProviderError("Не удалось обновить вход Grok. Подключите аккаунт снова.") from None
            locked.secret_ref = crypto.encrypt(json.dumps(data))
            # Persist a rotated refresh token before any upstream inference can fail.
            await session.commit()
    return wire_key(data)
