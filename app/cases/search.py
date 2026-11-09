"""Поиск похожих кейсов: эмбеддинги sentence-transformers + фолбэк на FTS5."""
from __future__ import annotations

import math
import re
from array import array

EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

_model = None


def _get_model():
    """Лениво грузит модель эмбеддингов. Падает исключением, если недоступна."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def embed_text(text: str) -> list[float]:
    """Возвращает вектор эмбеддинга текста. Может бросить исключение, если модель недоступна."""
    model = _get_model()
    vector = model.encode(text)
    return [float(x) for x in vector]


def vector_to_blob(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def blob_to_vector(blob: bytes) -> list[float]:
    vector = array("f")
    vector.frombytes(blob)
    return list(vector)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def fts_match_query(text: str) -> str:
    """Строит FTS5 MATCH-запрос: любое из слов текста (OR), с экранированием."""
    tokens = re.findall(r"\w+", text, re.UNICODE)
    if not tokens:
        # пустой запрос не должен быть синтаксической ошибкой для MATCH
        return '""'
    return " OR ".join(f'"{token}"' for token in tokens)
