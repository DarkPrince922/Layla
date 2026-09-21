from __future__ import annotations

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
