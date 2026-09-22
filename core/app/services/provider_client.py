"""Прямой клиент к провайдерам (OpenAI-совместимый и Anthropic).

Layla обращается к эндпоинту провайдера напрямую, используя его base_url и ключ.
Выбор ключа проходит через ротацию (KeyRotator): несколько ключей провайдера
чередуются, а на rate-limit уводятся в cooldown. Так чат/генерация работают без
необходимости регистрировать модели в LiteLLM.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ProviderKind
from app.models.provider import Provider, ProviderKey
from app.security import crypto
from app.services.rotation import KeyRotator, KeyState

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


async def _keys_for(session: AsyncSession, provider: Provider) -> list[tuple[str, str]]:
    """Вернуть список (key_id, secret) для провайдера: из ProviderKey либо одиночный."""
    rows = list(
        await session.scalars(select(ProviderKey).where(ProviderKey.provider_id == provider.id))
    )
    out: list[tuple[str, str]] = []
    for k in rows:
        try:
            out.append((k.id, crypto.decrypt(k.secret_ref)))
        except ValueError:
            continue
    if not out and provider.secret_ref:
        try:
            out.append((provider.id, crypto.decrypt(provider.secret_ref)))
        except ValueError:
            pass
    return out


async def pick_key(session: AsyncSession, provider: Provider) -> str | None:
    """Выбрать ключ через ротацию (или None, если ключей нет — для локальных)."""
    pairs = await _keys_for(session, provider)
    if not pairs:
        return None
    rotator = KeyRotator([KeyState(kid, secret) for kid, secret in pairs])
    chosen = rotator.pick()
    return chosen.secret if chosen else pairs[0][1]


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
    items = data.get("data", data if isinstance(data, list) else [])
    names: list[str] = []
    for it in items:
        mid = it.get("id") or it.get("name") if isinstance(it, dict) else None
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
                    raise RuntimeError("Провайдер вернул ошибку ответа")
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
                    raise RuntimeError("Провайдер вернул ошибку ответа")
                if evt.get("type") == "content_block_delta":
                    delta = evt.get("delta", {})
                    if delta.get("type") == "thinking_delta" and delta.get("thinking"):
                        yield ("reasoning", delta["thinking"])
                    elif delta.get("text"):
                        yield ("content", delta["text"])


async def complete(
    provider: Provider, key: str | None, model: str, messages: list[dict]
) -> str:
    """Не-стрим комплишн (для генерации Design). Reasoning отбрасывается."""
    parts = []
    async for kind, text in stream_chat(provider, key, model, messages):
        if kind == "content":
            parts.append(text)
    return "".join(parts)
