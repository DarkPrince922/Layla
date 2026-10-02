from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AgentRunCreate(BaseModel):
    task: str = Field(min_length=1)
    mode: str = "interactive"   # interactive | autonomous
    model: str | None = None
    persona_id: str | None = None
    max_workers: int = Field(default=3, ge=0, le=3)


class AgentStepOut(BaseModel):
    id: str
    ordinal: int
    role: str
    kind: str
    status: str
    requires_hitl: bool
    target: str | None = None
    command: str | None = None
    summary: str | None = None
    output: str | None = None
    worker_id: str | None = None

    model_config = {"from_attributes": True}


class WorkerCreate(BaseModel):
    task: str = Field(min_length=1, max_length=8000)
    role: Literal["explorer", "reviewer", "implementer"] = "explorer"


class AgentChatCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    run_id: str | None = None
    model: str | None = None
    mode: Literal["auto", "confirm", "plan"] = "auto"


class WorkerOut(BaseModel):
    id: str
    run_id: str
    job_id: str | None
    task: str
    role: str
    model: str
    status: str
    rounds: int
    result: str | None = None
    error: str | None = None
    messages: list[dict] = []
    model_config = {"from_attributes": True}


class AgentRunOut(BaseModel):
    id: str
    engagement_id: str | None = None
    task: str
    mode: str
    model: str | None = None
    status: str
    budget_used: dict = {}
    steps: list[AgentStepOut] = []
    workers: list[WorkerOut] = []

    model_config = {"from_attributes": True}


class TriageRequest(BaseModel):
    model: str | None = None


class TriageResult(BaseModel):
    finding_id: str
    verdict: str


class AgentConfigIn(BaseModel):
    preset: str = "minimal"
    role_models: dict = {}
    budgets: dict = {}
    diagnostics: dict = {}
    context: dict = {}


class AgentConfigOut(AgentConfigIn):
    pass
