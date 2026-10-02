from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class JobStep(BaseModel):
    text: str
    at: str | None = None


class JobOut(BaseModel):
    id: str
    chat_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    domain: str
    kind: str
    title: str
    status: str
    progress: float
    reasoning: str = ""
    steps: list[JobStep] = []
    result: dict[str, Any] = {}
    error: str | None = None

    model_config = {"from_attributes": True}
