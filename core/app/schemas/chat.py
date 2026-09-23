from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import Domain
from app.schemas.job import JobOut


class ChatCreate(BaseModel):
    domain: Domain = Domain.code
    title: str | None = None
    persona_id: str | None = None
    model: str | None = None
    provider_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None


class ChatOut(BaseModel):
    id: str
    domain: Domain
    title: str | None = None
    persona_id: str | None = None
    model: str | None = None
    provider_id: str | None = None
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
    model: str | None = None  # выбранная модель; закрепляется за чатом
    provider_id: str | None = None  # явный провайдер из пикера (имена моделей могут совпадать)
    # auto — агент сам применяет изменения; confirm — каждое изменение ждёт «Применить»;
    # plan — только чтение и план, без изменений.
    mode: Literal["auto", "confirm", "plan"] = "auto"


class DecisionRequest(BaseModel):
    approval_id: str = Field(min_length=1, max_length=200)
    decision: Literal["approve", "reject", "approve_all"]


class ChatRename(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class RollbackOut(BaseModel):
    restored: list[str]  # файлы, возвращённые к состоянию до выбранного ответа
    messages: int  # сколько ответов агента откатано


class ClearChatsOut(BaseModel):
    deleted: int
    skipped: int  # чаты с работающей задачей не удаляются
