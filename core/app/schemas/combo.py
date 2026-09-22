from __future__ import annotations

from pydantic import BaseModel, Field


class ComboCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    models: list[str] = []
    enabled: bool = True
    cooldown_seconds: int = 60


class ComboUpdate(BaseModel):
    name: str | None = None
    models: list[str] | None = None
    enabled: bool | None = None
    cooldown_seconds: int | None = None


class ComboOut(BaseModel):
    id: str
    name: str
    models: list[str] = []
    enabled: bool
    cooldown_seconds: int

    model_config = {"from_attributes": True}


class RouteResult(BaseModel):
    combo_id: str
    model: str | None = None
