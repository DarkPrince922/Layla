from __future__ import annotations

from pydantic import BaseModel, Field


class KnowledgeUpload(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)
    source: str | None = None
    domain: str | None = None
    persona_id: str | None = None


class KnowledgeDocOut(BaseModel):
    id: str
    title: str
    source: str | None = None
    domain: str | None = None
    chunk_count: int = 0

    model_config = {"from_attributes": True}


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = 5
    domain: str | None = None


class SearchHit(BaseModel):
    chunk_id: str
    doc_id: str
    ordinal: int
    content: str
    score: float
