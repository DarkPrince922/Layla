from __future__ import annotations

from pydantic import BaseModel

from app.models.enums import PersonaKind


class PersonaOut(BaseModel):
    id: str
    name: str
    kind: PersonaKind
    icon: str | None = None
    color: str | None = None
    instructions: str | None = None
    allowed_tools: list = []
    is_builtin: bool = False
    hitl_required: bool = True

    model_config = {"from_attributes": True}
