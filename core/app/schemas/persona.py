from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import PersonaKind

# Через редактор ролей меняется только доступ к файлам проекта. Остальные права
# (venue.exec, intel.lookup и т. п.) и HITL задаются встроенными ролями и не
# редактируются — так настройка роли не может ослабить проверки пентеста/OSINT.
FILE_TOOLS = ("files.read", "files.write")
Mode = Literal["auto", "confirm", "plan"]


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
    default_model: str | None = None
    default_mode: Mode | None = None

    model_config = {"from_attributes": True}


class PersonaCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    instructions: str | None = Field(default=None, max_length=20_000)
    icon: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=32)
    allowed_tools: list[str] = Field(default_factory=lambda: list(FILE_TOOLS))
    default_model: str | None = Field(default=None, max_length=200)
    default_mode: Mode | None = None


class PersonaUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    instructions: str | None = Field(default=None, max_length=20_000)
    icon: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=32)
    allowed_tools: list[str] | None = None  # учитываются только files.*; у встроенных — игнор
    default_model: str | None = Field(default=None, max_length=200)
    default_mode: Mode | None = None
