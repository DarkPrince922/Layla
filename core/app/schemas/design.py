from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.models.enums import DesignStack

_SHORT = 200
_Item = Annotated[str, Field(max_length=120)]


class DesignBrief(BaseModel):
    artifact_type: str = Field("Landing", max_length=_SHORT)
    direction: str = Field("Modern minimal", max_length=_SHORT)
    tone: str = Field("", max_length=_SHORT)
    theme: str = Field("both", max_length=_SHORT)
    pages: str = Field("Single", max_length=_SHORT)
    reference: str | None = Field(None, max_length=1000)
    brand: str | None = Field(None, max_length=_SHORT)
    notes: str | None = Field(None, max_length=6000)
    # Расширенные настройки. Пустое значение — «Авто»: решает модель.
    industry: str | None = Field(None, max_length=_SHORT)
    audience: str | None = Field(None, max_length=_SHORT)
    language: str | None = Field(None, max_length=60)
    layout: str | None = Field(None, max_length=_SHORT)
    palette: str | None = Field(None, max_length=_SHORT)
    accent_color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    fonts: str | None = Field(None, max_length=_SHORT)
    density: str | None = Field(None, max_length=_SHORT)
    radius: str | None = Field(None, max_length=_SHORT)
    effects: list[_Item] = Field(default_factory=list, max_length=24)
    sections: list[_Item] = Field(default_factory=list, max_length=24)
    content: str | None = Field(None, max_length=_SHORT)
    imagery: str | None = Field(None, max_length=_SHORT)
    icons: str | None = Field(None, max_length=_SHORT)
    animation: str | None = Field(None, max_length=_SHORT)
    interactivity: str | None = Field(None, max_length=_SHORT)
    device: str | None = Field(None, max_length=_SHORT)
    css: str | None = Field(None, max_length=_SHORT)
    accessibility: bool = False
    creativity: Literal["safe", "balanced", "bold", "wild"] = "balanced"


class DesignCreate(BaseModel):
    stack: DesignStack = DesignStack.html
    brief: DesignBrief
    model: str | None = None  # модель для генерации; иначе первая активная


class DesignFile(BaseModel):
    name: str
    content: str
    language: str = "html"


class DesignOut(BaseModel):
    id: str
    stack: DesignStack
    brief: dict = {}
    files: list = []
    design_system_ref: str | None = None
    project_id: str | None = None  # проект домена «Код», если макет отдан в разработку

    model_config = {"from_attributes": True}
