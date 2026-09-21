"""API базы знаний / RAG (спец. §5.9)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.knowledge import KnowledgeChunk, KnowledgeDoc
from app.models.user import User
from app.schemas.knowledge import (
    KnowledgeDocOut,
    KnowledgeUpload,
    SearchHit,
    SearchRequest,
)
from app.services import audit, rag
from app.services.auth import get_current_user

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("", response_model=list[KnowledgeDocOut])
async def list_docs(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeDoc]:
    rows = await session.scalars(
        select(KnowledgeDoc).where(KnowledgeDoc.owner_id == user.id).order_by(
            KnowledgeDoc.created_at.desc()
        )
    )
    return list(rows)


@router.post("", response_model=KnowledgeDocOut, status_code=201)
async def upload_doc(
    body: KnowledgeUpload,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeDoc:
    doc = await rag.index_document(
        session,
        owner_id=user.id,
        title=body.title,
        content=body.content,
        source=body.source,
        domain=body.domain,
        persona_id=body.persona_id,
    )
    await audit.record(session, actor=user.id, action="knowledge.index", target=body.title,
                       meta={"chunks": doc.chunk_count})
    await session.commit()
    return doc


@router.post("/search", response_model=list[SearchHit])
async def search_knowledge(
    body: SearchRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SearchHit]:
    hits = await rag.search(
        session, owner_id=user.id, query=body.query, top_k=body.top_k, domain=body.domain
    )
    return [SearchHit(**h) for h in hits]


@router.delete("/{doc_id}", status_code=204)
async def delete_doc(
    doc_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    doc = await session.get(KnowledgeDoc, doc_id)
    if doc is None or doc.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Документ не найден")
    await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc.id))
    await session.delete(doc)
    await session.commit()
