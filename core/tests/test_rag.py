"""Тесты RAG: эмбеддинги, чанкинг, релевантность поиска (спец. §5.9)."""
from __future__ import annotations

import pytest

from app.services import embeddings, rag


def test_embedding_is_deterministic_and_normalized():
    a = embeddings.embed_text("привет мир")
    b = embeddings.embed_text("привет мир")
    assert a == b  # стабилен между вызовами (важно для сохранённых векторов)
    norm = sum(x * x for x in a) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_cosine_similar_texts_score_higher():
    q = embeddings.embed_text("настройка ротации ключей провайдера")
    near = embeddings.embed_text("ротация ключей провайдера при лимите")
    far = embeddings.embed_text("рецепт борща со свёклой")
    assert embeddings.cosine(q, near) > embeddings.cosine(q, far)


def test_chunk_text_overlap():
    text = " ".join(f"w{i}" for i in range(2000))
    chunks = rag.chunk_text(text, size=800, overlap=100)
    assert len(chunks) >= 2
    assert all(chunks)


@pytest.mark.asyncio
async def test_index_and_search(client):
    await client.post(
        "/api/auth/register",
        json={"email": "kb@example.com", "password": "hunter2hunter2"},
    )
    await client.post(
        "/api/knowledge",
        json={
            "title": "Руководство по пентесту",
            "content": "Scope enforcement проверяет каждую цель по allow-list перед выполнением. "
            "Egress fail-closed блокирует трафик при сбое Tor или proxy маршрута.",
            "domain": "pentest",
        },
    )
    await client.post(
        "/api/knowledge",
        json={"title": "Заметки о кофе", "content": "Эспрессо готовится под давлением девять бар."},
    )

    docs = (await client.get("/api/knowledge")).json()
    assert len(docs) == 2

    r = await client.post(
        "/api/knowledge/search", json={"query": "как работает egress fail-closed", "top_k": 3}
    )
    assert r.status_code == 200
    hits = r.json()
    assert hits
    # Наиболее релевантный чанк — про egress/scope, а не про кофе.
    assert "egress" in hits[0]["content"].lower() or "scope" in hits[0]["content"].lower()


@pytest.mark.asyncio
async def test_delete_doc_removes_chunks(client):
    await client.post(
        "/api/auth/register",
        json={"email": "kb2@example.com", "password": "hunter2hunter2"},
    )
    doc = (
        await client.post(
            "/api/knowledge", json={"title": "T", "content": "one two three four five"}
        )
    ).json()
    await client.delete(f"/api/knowledge/{doc['id']}")
    # Поиск больше ничего не находит.
    hits = (await client.post("/api/knowledge/search", json={"query": "two"})).json()
    assert hits == []
