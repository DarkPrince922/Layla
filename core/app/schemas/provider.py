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
