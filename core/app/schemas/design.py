from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import DesignStack


class DesignBrief(BaseModel):
    artifact_type: str = "Landing"
    direction: str = "Modern minimal"
    tone: str = ""
    theme: str = "both"
    pages: str = "Single"
    reference: str | None = None
    brand: str | None = None
    notes: str | None = None


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

    model_config = {"from_attributes": True}
