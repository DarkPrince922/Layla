from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    workspace_id: str | None = None
    path: str | None = None


class RepoImport(BaseModel):
    repo_url: str = Field(min_length=1)
    name: str | None = None
    workspace_id: str | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    repo_url: str | None = None
    path: str | None = None

    model_config = {"from_attributes": True}


class FileNode(BaseModel):
    name: str
    path: str
    is_dir: bool


class FileContent(BaseModel):
    path: str
    content: str
