from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import SubjectType
from app.services.intel_targets import normalize_target


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject_type: SubjectType = SubjectType.domain
    subject: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def normalize(self) -> CaseCreate:
        if self.subject_type == SubjectType.domain:
            self.subject = normalize_target(self.subject)
        elif any(ord(c) < 32 for c in self.subject):
            raise ValueError("Название не должно содержать управляющие символы")
        return self


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    subject_type: SubjectType
    subject: str
    created_at: datetime
    updated_at: datetime
    artifact_count: int = 0
    lookup_count: int = 0


class ManualArtifactIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=8000)
    source_url: str = Field(min_length=1, max_length=2048)

    @field_validator("source_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        try:
            url = urlsplit(value)
            valid = url.scheme in {"https", "http"} and url.hostname and not url.username
        except ValueError:
            valid = False
        if not valid or any(c.isspace() for c in value):
            raise ValueError("Источник должен быть HTTP(S)-ссылкой без логина и пароля")
        return value


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    provider: str
    target: str
    kind: str
    title: str
    summary: str
    source_url: str
    data: dict
    created_at: datetime


class LookupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    provider: str
    target: str
    status: str
    error_code: str | None
    error: str | None
    artifact_count: int
    duplicate_count: int
    created_at: datetime


class SourceOut(BaseModel):
    provider: str
    source_url: str
    artifact_count: int
