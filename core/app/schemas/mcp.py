from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import McpTransport


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    transport: McpTransport = McpTransport.stdio
    command: str | None = None   # для stdio, напр. "npx -y @modelcontextprotocol/server-filesystem"
    url: str | None = None       # для http
    enabled: bool = True
    personas: list[str] = []
    env: dict[str, str] = {}


class McpServerUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    personas: list[str] | None = None
    command: str | None = None
    url: str | None = None


class McpServerOut(BaseModel):
    id: str
    name: str
    transport: McpTransport
    command: str | None = None
    url: str | None = None
    enabled: bool
    personas: list = []
    env_keys: list[str] = []  # только имена переменных, без значений

    model_config = {"from_attributes": True}


class McpToolInfo(BaseModel):
    name: str
    description: str | None = None


class McpTestResult(BaseModel):
    ok: bool
    tools: list[McpToolInfo] = []
    error: str | None = None
