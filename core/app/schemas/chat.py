from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import Domain
from app.schemas.job import JobOut


class ChatCreate(BaseModel):
    domain: Domain = Domain.code
    title: str | None = None
    persona_id: str | None = None
    model: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None


class ChatOut(BaseModel):
    id: str
    domain: Domain
    title: str | None = None
    persona_id: str | None = None
    model: str | None = None
    project_id: str | None = None

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    meta: dict = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class ChatDetail(ChatOut):
    messages: list[MessageOut] = Field(default_factory=list)
    last_job: JobOut | None = None


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    request_id: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = None  # переопределяет модель чата на этот запрос
