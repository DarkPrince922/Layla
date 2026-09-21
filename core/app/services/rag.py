"""RAG: чанкинг, индексация и поиск по базе знаний (спец. §5.9)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk, KnowledgeDoc
from app.services import embeddings


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """Нарезать текст на перекрывающиеся чанки по словам."""
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    step = max(1, size - overlap)
    for start in range(0, len(words), step):
        chunk = " ".join(words[start : start + size])
        if chunk:
            chunks.append(chunk)
        if start + size >= len(words):
            break
    return chunks


async def index_document(
    session: AsyncSession,
    *,
    owner_id: str,
    title: str,
    content: str,
    source: str | None = None,
    domain: str | None = None,
    persona_id: str | None = None,
) -> KnowledgeDoc:
    """Создать документ, нарезать на чанки и проиндексировать эмбеддинги."""
    doc = KnowledgeDoc(
        owner_id=owner_id, title=title, source=source, domain=domain, persona_id=persona_id
    )
    session.add(doc)
    await session.flush()

    pieces = chunk_text(content)
    vectors = embeddings.embed_texts(pieces)
    for i, (piece, vec) in enumerate(zip(pieces, vectors)):
        session.add(
            KnowledgeChunk(
                doc_id=doc.id,
                owner_id=owner_id,
                ordinal=i,
                content=piece,
                embedding=vec,
                domain=domain,
            )
        )
    doc.chunk_count = len(pieces)
    await session.flush()
    return doc


async def search(
    session: AsyncSession,
    *,
    owner_id: str,
    query: str,
    top_k: int = 5,
    domain: str | None = None,
) -> list[dict]:
    """Найти наиболее релевантные чанки по косинусному сходству."""
    q_vec = embeddings.embed_text(query)
    stmt = select(KnowledgeChunk).where(KnowledgeChunk.owner_id == owner_id)
    if domain:
        stmt = stmt.where(KnowledgeChunk.domain == domain)
    rows = list(await session.scalars(stmt))
    scored = [
        {
            "chunk_id": c.id,
            "doc_id": c.doc_id,
            "ordinal": c.ordinal,
            "content": c.content,
            "score": embeddings.cosine(q_vec, c.embedding or []),
        }
        for c in rows
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
