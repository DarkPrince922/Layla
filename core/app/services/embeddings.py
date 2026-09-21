"""Эмбеддинги для RAG (спец. §5.9).

По умолчанию используется офлайн детерминированный векторизатор (hashing trick):
он не требует внешних сервисов, стабилен и позволяет RAG работать полностью
локально и в тестах. При наличии настроенной модели эмбеддингов запрос можно
направить в LiteLLM (метод LiteLLMClient.embeddings) — переключается на уровне
вызова.
"""
from __future__ import annotations

import hashlib
import math
import re

DIM = 256
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _stable_bucket(token: str) -> int:
    # Стабильный между процессами хеш (в отличие от встроенного hash()).
    digest = hashlib.md5(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % DIM


def embed_text(text: str) -> list[float]:
    """Детерминированный офлайн-эмбеддинг фиксированной размерности (нормализован)."""
    vec = [0.0] * DIM
    for tok in _tokenize(text):
        vec[_stable_bucket(tok)] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def embed_texts(texts: list[str]) -> list[list[float]]:
    return [embed_text(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
