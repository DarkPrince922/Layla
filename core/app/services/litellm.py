"""Интеграция с прокси LiteLLM (спец. §2, §5.1).

Чат-бэкенд ходит ТОЛЬКО в LiteLLM. Профили провайдеров Layla транслируются в
``model_list`` LiteLLM: несколько ключей одного профиля дают несколько записей с
одинаковым ``model_name`` — это включает ротацию/fallback на стороне роутера
LiteLLM. Функция построения списка — чистая и покрыта тестами; сетевой клиент
вынесен отдельно.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import get_settings
from app.models.enums import ProviderKind
from app.models.provider import Provider

# Префиксы провайдера для LiteLLM ("model" в litellm_params).
_KIND_PREFIX = {
    ProviderKind.openai_compatible: "openai",
    ProviderKind.anthropic: "anthropic",
    ProviderKind.custom: "openai",
}


def build_model_list(
    items: list[tuple[Provider, list[tuple[str, str]]]],
) -> list[dict[str, Any]]:
    """Построить ``model_list`` LiteLLM.

    ``items`` — список пар ``(provider, [(key_id, secret), ...])`` только по
    активным профилям и здоровым ключам. Профиль без ключей всё равно даёт одну
    запись (например, для локальных провайдеров без ключа).
    """
    model_list: list[dict[str, Any]] = []
    for provider, keys in items:
        if not provider.enabled:
            continue
        model_name = provider.default_model or provider.name
        prefix = _KIND_PREFIX.get(provider.kind, "openai")
        base_params: dict[str, Any] = {"model": f"{prefix}/{model_name}"}
        if provider.base_url:
            base_params["api_base"] = provider.base_url

        if keys:
            # Одна запись на ключ с общим model_name → ротация в роутере LiteLLM.
            for key_id, secret in keys:
                params = dict(base_params)
                params["api_key"] = secret
                model_list.append(
                    {
                        "model_name": model_name,
                        "litellm_params": params,
                        "model_info": {"layla_provider_id": provider.id, "layla_key_id": key_id},
                    }
                )
        else:
            params = dict(base_params)
            params["api_key"] = "not-needed"  # локальные OpenAI-совместимые
            model_list.append(
                {
                    "model_name": model_name,
                    "litellm_params": params,
                    "model_info": {"layla_provider_id": provider.id},
                }
            )
    return model_list


def active_model_names(providers: list[Provider]) -> list[str]:
    """Имена моделей активных профилей — для пикера модели."""
    return [
        (p.default_model or p.name)
        for p in providers
        if p.enabled and p.active
    ]


class LiteLLMClient:
    """Тонкий асинхронный клиент к OpenAI-совместимому эндпоинту LiteLLM."""

    def __init__(self, base_url: str | None = None, master_key: str | None = None) -> None:
        settings = get_settings()
        self._base = (base_url or settings.litellm_base_url).rstrip("/")
        self._key = master_key or settings.litellm_master_key

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._key:
            h["Authorization"] = f"Bearer {self._key}"
        return h

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self._base}/health/liveliness", headers=self._headers())
                return r.status_code < 500
        except httpx.HTTPError:
            return False

    async def stream_chat(
        self, model: str, messages: list[dict[str, str]], **kwargs: Any
    ) -> AsyncIterator[str]:
        """Стримить дельты контента из LiteLLM (OpenAI SSE) как строки текста."""
        payload = {"model": model, "messages": messages, "stream": True, **kwargs}
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self._base}/v1/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = (
                        chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                    )
                    if delta:
                        yield delta
