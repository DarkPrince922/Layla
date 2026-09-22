from __future__ import annotations

from pydantic import BaseModel, Field


class AgentRunCreate(BaseModel):
    task: str = Field(min_length=1)
    mode: str = "interactive"   # interactive | autonomous
    model: str | None = None
    persona_id: str | None = None


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
