"""Wire contract shared by the API and the bundled MCP server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.services.intel_targets import normalize_target

IntelProviderName = Literal["shodan", "virustotal", "securitytrails", "urlscan"]


class IntelKeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        if not raw or any(ord(c) < 33 or ord(c) > 126 for c in raw):
            raise ValueError("Ключ должен содержать печатные символы без пробелов")
        return SecretStr(raw)


class IntelProviderOut(BaseModel):
    provider: IntelProviderName
    name: str
    docs_url: str
    tool_name: str
    supports_ip: bool
    key_required: bool = True
    configured: bool = False
    key_masked: str | None = None
    passive: bool = True


class LookupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: IntelProviderName
    target: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("target")
    @classmethod
    def normalize(cls, value: str | None) -> str | None:
        return normalize_target(value) if value is not None else None


class IntelArtifact(BaseModel):
    kind: str = Field(max_length=40)
    title: str = Field(max_length=300)
    summary: str = Field(max_length=8000)
    source_url: str = Field(max_length=2048)
    data: dict = Field(default_factory=dict)


class IntelResult(BaseModel):
    status: Literal["ok", "empty", "error"]
    artifacts: list[IntelArtifact] = Field(default_factory=list, max_length=20)
    error_code: str | None = None
    error: str | None = None
