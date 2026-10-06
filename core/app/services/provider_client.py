"""Прямой клиент к провайдерам (OpenAI-совместимый и Anthropic).

Layla обращается к эндпоинту провайдера напрямую, используя его base_url и ключ.
Ключи провайдера берутся по порядку (KeyRing): ключ, который провайдер отклонил
(неверный, без денег, упёрся в лимит), уступает место следующему. Так чат и
генерация работают без необходимости регистрировать модели в LiteLLM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import KeyStatus, ProviderKind
from app.models.provider import Provider, ProviderKey
from app.security import crypto

logger = logging.getLogger("layla.provider")


class ProviderResponseError(RuntimeError):
    """Провайдер прислал ошибку в потоке ответа; транзиентную стоит повторить."""

_reasoning_observer: ContextVar[Callable[[str], Awaitable[None]] | None] = ContextVar('provider_reasoning_observer', default=None)


@contextmanager
def capture_reasoning(callback):
    """Observe only reasoning explicitly returned by a provider, per async task."""
    token = _reasoning_observer.set(callback)
    try:
        yield
    finally:
        _reasoning_observer.reset(token)


_DEFAULT_BASE = {
    ProviderKind.openai_compatible: "https://api.openai.com/v1",
    ProviderKind.anthropic: "https://api.anthropic.com",
    ProviderKind.custom: "https://api.openai.com/v1",
}


def _base(provider: Provider) -> str:
    base = provider.base_url or _DEFAULT_BASE.get(provider.kind, "https://api.openai.com/v1")
    return base.rstrip("/")


def endpoint(provider: Provider, resource: str) -> str:
    base = _base(provider)
    for suffix in ("/chat/completions", "/messages", "/models"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    if _is_anthropic_native(provider) and not base.endswith("/v1"):
        base += "/v1"
    return f"{base}/{resource}"


def _is_anthropic_native(provider: Provider) -> bool:
    return provider.kind == ProviderKind.anthropic


# Ключи, которые провайдер недавно отклонил: key_id -> до какого момента (monotonic) их
# не брать первыми. В памяти процесса: статус ключа в «Настройках» остаётся за пользователем.
_RESTING: dict[str, float] = {}
KEY_REST = 300.0


class KeyRing:
    """Ключи провайдера на одну задачу, в стабильном порядке.

    Отказ из-за ключа (401/402/403/429) — переходим к следующему ещё не опробованному;
    отказавший ключ несколько минут ставится в конец и для других задач.
    """

    def __init__(self, keys: list[tuple[str, str, str | None]]) -> None:
        now = time.monotonic()
        self._keys = sorted(keys, key=lambda k: _RESTING.get(k[0], 0.0) > now)  # sorted стабилен
        self._index = 0
        self._tried = {0}

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def current(self) -> str | None:
        return self._keys[self._index][1] if self._keys else None

    @property
    def label(self) -> str:
        if not self._keys:
            return ""
        return self._keys[self._index][2] or f"ключ {self._index + 1}"

    def rotate(self, rest: float | None = None) -> bool:
        """Отложить текущий ключ; True — есть ещё не опробованный, он стал текущим."""
        if not self._keys:
            return False
        _RESTING[self._keys[self._index][0]] = time.monotonic() + max(KEY_REST, rest or 0.0)
        for index in range(len(self._keys)):
            if index not in self._tried:
                self._index = index
                self._tried.add(index)
                return True
        return False


async def _keys_for(session: AsyncSession, provider: Provider) -> list[tuple[str, str, str | None]]:
    """Ключи провайдера (key_id, secret, label): из ProviderKey либо одиночный.

    Порядок стабильный (по дате добавления): раньше он зависел от того, как БД
    вернула строки, и сломанный ключ попадался «через раз». Отключённые ключи не
    берём, упёршиеся в лимит — в последнюю очередь.
    """
    from app.services import grok_oauth
    oauth_key = await grok_oauth.access_key(session, provider)
    if oauth_key:
        return [(provider.id, oauth_key, "Grok account")]
    rows = list(
        await session.scalars(
            select(ProviderKey)
            .where(ProviderKey.provider_id == provider.id, ProviderKey.status != KeyStatus.disabled)
            .order_by(ProviderKey.created_at, ProviderKey.id)
        )
    )
    rows.sort(key=lambda k: k.status != KeyStatus.active)
    out: list[tuple[str, str, str | None]] = []
    for k in rows:
        try:
            out.append((k.id, crypto.decrypt(k.secret_ref), k.label))
        except ValueError:
            continue
    if not out and provider.secret_ref:
        try:
            out.append((provider.id, crypto.decrypt(provider.secret_ref), None))
        except ValueError:
            pass
    return out


async def key_ring(session: AsyncSession, provider: Provider) -> KeyRing:
    return KeyRing(await _keys_for(session, provider))


async def pick_key(session: AsyncSession, provider: Provider) -> str | None:
    """Первый подходящий ключ (или None, если ключей нет — для локальных)."""
    return (await key_ring(session, provider)).current


async def resolve_provider(
    session: AsyncSession, owner_id: str, model: str
) -> Provider | None:
    """Найти активный провайдер, которому принадлежит модель."""
    providers = list(
        await session.scalars(
            select(Provider).where(
                Provider.owner_id == owner_id,
                Provider.enabled == True,
                Provider.active == True,
            )
        )
    )
    # 1) Провайдер, у которого модель есть в списке моделей.
    for p in providers:
        names = [m["name"] for m in (p.models or [])]
        if model in names:
            return p
    # 2) Совпадение с default_model / именем профиля.
    for p in providers:
        if (p.default_model or p.name) == model or p.name == model:
            return p
    # 3) Иначе — первый активный (единственный агрегатор).
    return providers[0] if providers else None


def _headers(provider: Provider, key: str | None) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    from app.services.grok_oauth import PROTOCOL_VERSION, TOKEN_PREFIX, wire_credentials
    if key and key.startswith(TOKEN_PREFIX):
        data = wire_credentials(key)
        h["Authorization"] = "Bearer " + data["access_token"]
        h["X-XAI-Token-Auth"] = "xai-grok-cli"
        h["x-authenticateresponse"] = "authenticate-response"
        h["x-grok-client-identifier"] = "layla"
        h["x-grok-client-version"] = PROTOCOL_VERSION
        if data.get("user_id"):
            h["x-userid"] = data["user_id"]
        return h
    if _is_anthropic_native(provider):
        if key:
            h["x-api-key"] = key
        h["anthropic-version"] = "2023-06-01"
    elif key:
        h["Authorization"] = f"Bearer {key}"
    return h


async def list_models(provider: Provider, key: str | None) -> list[str]:
    """Получить список моделей у провайдера (OpenAI-совместимый /models)."""
    if _is_anthropic_native(provider):
        url = endpoint(provider, "models")
    else:
        url = endpoint(provider, "models")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers(provider, key))
        resp.raise_for_status()
        data = resp.json()
    items = data if isinstance(data, list) else data.get("data", [])
    names: list[str] = []
    for it in items:
        if isinstance(it, dict) and it.get("hidden"):
            continue
        from app.services.grok_oauth import TOKEN_PREFIX
        if isinstance(it, dict):
            mid = (it.get("model") if key and key.startswith(TOKEN_PREFIX) else None) or it.get("id") or it.get("name")
        else:
            mid = None
        if mid:
            names.append(mid)
    return names


async def stream_chat(
    provider: Provider, key: str | None, model: str, messages: list[dict]
) -> AsyncIterator[tuple[str, str]]:
    """Стримить дельты от провайдера как пары (kind, text).

    kind == "content" — видимый ответ; kind == "reasoning" — размышление модели
    (reasoning-модели вроде kimi/deepseek-r1/o-серии отдают его отдельно).
    """
    from app.services.grok_oauth import TOKEN_PREFIX
    if key and key.startswith(TOKEN_PREFIX):
        from app.services.grok_responses import stream_turn
        async for kind, value in stream_turn(provider, key, model, messages, []):
            if kind in ("content", "reasoning"):
                yield kind, value
        return
    if _is_anthropic_native(provider):
        async for pair in _stream_anthropic(provider, key, model, messages):
            yield pair
        return

    payload = {"model": model, "messages": messages, "stream": True}
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15)) as client:  # noqa: SIM117
        async with client.stream(
            "POST", endpoint(provider, "chat/completions"),
            headers=_headers(provider, key), json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    err = chunk["error"]
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    logger.warning("Ошибка в потоке ответа провайдера: %s", str(err)[:1000])
                    raise ProviderResponseError(("Провайдер вернул ошибку ответа: " + str(msg)[:300]).strip())
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                d = choices[0].get("delta") or {}
                reasoning = d.get("reasoning_content") or d.get("reasoning")
                if reasoning:
                    yield ("reasoning", reasoning)
                content = d.get("content")
                if content:
                    yield ("content", content)


async def _stream_anthropic(
    provider: Provider, key: str | None, model: str, messages: list[dict]
) -> AsyncIterator[tuple[str, str]]:
    # Anthropic /v1/messages: system отдельным полем, роли user/assistant.
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    conv = [m for m in messages if m["role"] in ("user", "assistant")]
    payload = {
        "model": model,
        "system": system or None,
        "messages": conv,
        "max_tokens": 8192,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15)) as client:  # noqa: SIM117
        async with client.stream(
            "POST", endpoint(provider, "messages"),
            headers=_headers(provider, key), json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                try:
                    evt = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") == "error":
                    err = evt.get("error") or evt
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    logger.warning("Ошибка в потоке ответа провайдера: %s", str(err)[:1000])
                    raise ProviderResponseError(("Провайдер вернул ошибку ответа: " + str(msg)[:300]).strip())
                if evt.get("type") == "content_block_delta":
                    delta = evt.get("delta", {})
                    if delta.get("type") == "thinking_delta" and delta.get("thinking"):
                        yield ("reasoning", delta["thinking"])
                    elif delta.get("text"):
                        yield ("content", delta["text"])


async def complete(
    provider: Provider, key: str | None, model: str, messages: list[dict]
) -> str:
    """Collect the visible response; optionally observe provider-returned reasoning."""
    parts = []
    async for kind, text in stream_chat(provider, key, model, messages):
        if kind == "content":
            parts.append(text)
        elif kind == "reasoning" and (observer := _reasoning_observer.get()) is not None:
            await observer(text)
    return "".join(parts)


async def complete_with_retry(provider, key, model, messages, *, on_retry=None, timeout=None):
    from app.services import provider_errors
    return await provider_errors.retry_request(lambda: complete(provider, key, model, messages),
                                               on_retry=on_retry, timeout=timeout)


async def stream_chat_with_retry(provider, key, model, messages):
    """Retract failed partial content before restarting the same model request."""
    from app.services import provider_errors
    for attempt in range(len(provider_errors.RETRY_DELAYS) + 1):
        shown = 0
        try:
            async for kind, text in stream_chat(provider, key, model, messages):
                if kind == "content":
                    shown += len(text)
                yield kind, text
            return
        except Exception as exc:
            if not provider_errors.should_retry_agent(exc) or attempt == len(provider_errors.RETRY_DELAYS):
                raise
            if shown:
                yield "retract", shown
            delay = provider_errors.retry_delay(exc, attempt + 1)
            yield "retry", {"attempt": attempt + 1, "max": len(provider_errors.RETRY_DELAYS), "delay": delay}
            await asyncio.sleep(delay)
