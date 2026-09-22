from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    workspace_id: str | None = None

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Укажите название проекта")
        return value.strip()


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
    sha256: str


class FileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=1_000_000)
    # Required null for create; the read version is required for edit.
    expected_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")


class FileChange(BaseModel):
    path: str
    operation: Literal["create", "edit", "delete"]
    diff: str
    before_sha256: str | None
    after_sha256: str | None
