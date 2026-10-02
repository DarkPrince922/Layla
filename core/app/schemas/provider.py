from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import ProviderKind


class ProviderCreate(BaseModel):
    name: str = Field(max_length=120)
    kind: ProviderKind = ProviderKind.openai_compatible
    base_url: str | None = None
    default_model: str | None = None
    enabled: bool = True
    active: bool = False
    # Optional single API key; stored encrypted, never returned in plaintext.
    api_key: str | None = None


class ProviderOut(BaseModel):
    id: str
    name: str
    kind: ProviderKind
    base_url: str | None = None
    default_model: str | None = None
    enabled: bool
    active: bool
    sort_order: int
    has_secret: bool = False

    model_config = {"from_attributes": True}


class ProviderKeyCreate(BaseModel):
    api_key: str = Field(min_length=1)
    label: str | None = None


class ProviderKeyOut(BaseModel):
    id: str
    label: str | None = None
    status: str
    masked: str = ""

    model_config = {"from_attributes": True}


class KeyStatusUpdate(BaseModel):
    # active | rate_limited | exhausted | disabled
    status: str


class AccountsHealth(BaseModel):
    profiles: int
    active_models: int
    oauth_accounts: int
    quota_limited: int


class ModelOut(BaseModel):
    name: str
    provider: str
    provider_id: str


class ProviderModelInfo(BaseModel):
    name: str
    enabled: bool = True
    # Умеет ли модель вызывать инструменты (работа с файлами). Выключается само,
    # если провайдер отклонил инструменты, и вручную — в настройках.
    tools: bool = True
    # Макс. токенов одного ответа. max_output_manual=False — «Авто»: Layla подбирает
    # сама (растёт, если ответ не влез); True — жёсткий потолок, заданный вручную.
    max_output: int | None = Field(default=None, ge=256, le=1_000_000)
    max_output_manual: bool = False
    # Контекст модели в токенах: столько истории Layla отправляет, пропуская старое.
    context: int | None = Field(default=None, ge=1024, le=10_000_000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    # Бюджет размышлений на один шаг, токенов. None — «Авто» (AUTO_REASONING_BUDGET),
    # 0 — без ограничения.
    reasoning_budget: int | None = Field(default=None, ge=0, le=1_000_000)
    # Параметры, от которых провайдер отказался (только для показа).
    dropped: list[str] = Field(default_factory=list)
    # Только во входящем PUT: забыть всё, что Layla подобрала сама, и начать с нуля.
    reset: bool = False


class ProviderModelsUpdate(BaseModel):
    models: list[ProviderModelInfo]
