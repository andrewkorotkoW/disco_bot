"""Тесты app/cases: CRUD хранилища, поиск похожих (эмбеддинги) и FTS5-фолбэк."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from app.cases import CaseStore
from app.cases import search as case_search

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "tickets.json"

EMBED_DIM = 32


def _load_tickets() -> list[dict]:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


def _fake_embed_text(text: str) -> list[float]:
    """Детерминированный bag-of-words эмбеддер (md5, не зависит от PYTHONHASHSEED)."""
    vector = [0.0] * EMBED_DIM
    for token in re.findall(r"\w+", text.lower(), re.UNICODE):
        idx = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % EMBED_DIM
        vector[idx] += 1.0
    return vector


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "cases.db")
    s = CaseStore(db_path)
    yield s
    s.close()


@pytest.fixture
def fake_embedder(monkeypatch):
    """Подменяет загрузку sentence-transformers на детерминированную векторизацию."""
    monkeypatch.setattr(case_search, "embed_text", _fake_embed_text)
    return _fake_embed_text


@pytest.fixture
def unavailable_model(monkeypatch):
    """Имитирует недоступность модели эмбеддингов (нет сети/весов)."""

    def _raise(*args, **kwargs):
        raise RuntimeError("sentence-transformers model unavailable in test env")

    monkeypatch.setattr(case_search, "_get_model", _raise)
    return _raise


@pytest.fixture
def tickets():
    return _load_tickets()


def _embedding_row(store, case_id):
    return store._conn.execute(
        "SELECT * FROM embeddings WHERE case_id = ?", (case_id,)
    ).fetchone()


def _decision_rows(store, case_id):
    return store._conn.execute(
        "SELECT * FROM decisions WHERE case_id = ? ORDER BY id", (case_id,)
    ).fetchall()


def _add_resolved(store, ticket):
    case = store.add_case(
        ticket["ticket_text"],
        source_channel_id=ticket["source_channel_id"],
        source_message_id=ticket["source_message_id"],
        player_name=ticket["player_name"],
        player_id=ticket["player_id"],
        category=ticket["category"],
        summary=ticket["summary"],
    )
    store.resolve_case(
        case.id,
        admin_id=ticket["decision"]["admin_id"],
        decision_text=ticket["decision"]["decision_text"],
    )
    return case


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


def test_add_case_minimal_defaults(store):
    case = store.add_case("Текст тикета без доп. полей")

    assert case.id is not None
    assert case.ticket_text == "Текст тикета без доп. полей"
    assert case.status == "open"
    assert case.source_channel_id is None
    assert case.source_message_id is None
    assert case.player_name is None
    assert case.player_id is None
    assert case.category is None
    assert case.summary is None
    datetime.fromisoformat(case.created_at)  # не пустая и валидная ISO-дата


def test_add_case_full_fields_round_trip(store):
    case = store.add_case(
        "Текст тикета",
        source_channel_id=111,
        source_message_id=222,
        player_name="Vasya",
        player_id=333,
        category="читы",
        summary="краткое summary",
    )

    fetched = store.get_case(case.id)
    assert fetched.source_channel_id == 111
    assert fetched.source_message_id == 222
    assert fetched.player_name == "Vasya"
    assert fetched.player_id == 333
    assert fetched.category == "читы"
    assert fetched.summary == "краткое summary"
    assert fetched.status == "open"


def test_get_case_missing_returns_none(store):
    assert store.get_case(999999) is None


def test_resolve_case_writes_decision_and_status(store):
    case = store.add_case("Тикет про баг", category="баг базы")

    decision = store.resolve_case(case.id, admin_id=42, decision_text="Исправлено вручную")

    assert decision.case_id == case.id
    assert decision.admin_id == 42
    assert decision.decision_text == "Исправлено вручную"
    assert decision.decided_at

    resolved_case = store.get_case(case.id)
    assert resolved_case.status == "resolved"

    rows = _decision_rows(store, case.id)
    assert len(rows) == 1
    assert rows[0]["admin_id"] == 42
    assert rows[0]["decision_text"] == "Исправлено вручную"


def test_resolve_case_missing_raises_value_error(store):
    with pytest.raises(ValueError):
        store.resolve_case(123456, admin_id=1, decision_text="x")


def test_resolve_case_recomputes_embedding(store, fake_embedder):
    case = store.add_case("Текст про читы", summary="сводка", category="читы")
    assert _embedding_row(store, case.id) is None  # до резолва эмбеддинга нет

    store.resolve_case(case.id, admin_id=1, decision_text="решение")

    row = _embedding_row(store, case.id)
    assert row is not None
    assert row["model_name"] == case_search.EMBEDDING_MODEL_NAME
    expected_vector = _fake_embed_text("Текст про читы сводка")
    stored_vector = case_search.blob_to_vector(row["vector"])
    assert stored_vector == pytest.approx(expected_vector)


def test_resolve_case_tolerates_embedding_failure(store, unavailable_model):
    case = store.add_case("Текст тикета", category="читы")

    decision = store.resolve_case(case.id, admin_id=1, decision_text="решение")

    assert decision.decision_text == "решение"
    assert store.get_case(case.id).status == "resolved"
    assert _embedding_row(store, case.id) is None


# --------------------------------------------------------------------------
# find_similar — метод "embedding" (фейковый детерминированный эмбеддер)
# --------------------------------------------------------------------------


def test_find_similar_embedding_returns_exact_match_top(store, fake_embedder, tickets):
    pool = [tickets[0], tickets[1], tickets[4], tickets[5]]  # читы + потеря лута
    cases = [_add_resolved(store, t) for t in pool]

    # эмбеддинг кейса считается от ticket_text + summary (см. resolve_case) —
    # повторяем ту же конкатенацию, чтобы гарантировать cosine == 1.0
    query = f"{tickets[0]['ticket_text']} {tickets[0]['summary']}"
    results = store.find_similar(query, k=5)

    assert results, "ожидался хотя бы один результат"
    top_case, top_decision, top_score, top_method = results[0]
    assert top_method == "embedding"
    assert top_case.id == cases[0].id
    assert top_score == pytest.approx(1.0, abs=1e-6)
    assert top_decision is not None
    assert top_decision.decision_text == tickets[0]["decision"]["decision_text"]

    scores = [r[2] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_find_similar_embedding_category_filter(store, fake_embedder, tickets):
    cheaty_case = _add_resolved(store, tickets[0])  # category читы
    loot_case_a = _add_resolved(store, tickets[4])  # category потеря лута
    loot_case_b = _add_resolved(store, tickets[5])  # category потеря лута

    query = f"{tickets[0]['ticket_text']} {tickets[0]['summary']}"
    results = store.find_similar(query, k=5, category="потеря лута")

    returned_ids = {case.id for case, _, _, _ in results}
    assert cheaty_case.id not in returned_ids
    assert returned_ids <= {loot_case_a.id, loot_case_b.id}
    for case, _, _, method in results:
        assert case.category == "потеря лута"
        assert method == "embedding"


def test_find_similar_embedding_respects_k(store, fake_embedder, tickets):
    for t in tickets[:6]:
        _add_resolved(store, t)

    query = "Общий текст про баги и читы на сервере"
    full = store.find_similar(query, k=100)
    limited = store.find_similar(query, k=2)

    assert len(limited) == 2
    assert [c.id for c, _, _, _ in limited] == [c.id for c, _, _, _ in full[:2]]


def test_find_similar_embedding_unresolved_case_not_matched(store, fake_embedder, tickets):
    # у неразрешённого кейса эмбеддинг ещё не посчитан -> не участвует в поиске похожих
    open_case = store.add_case(tickets[0]["ticket_text"], category=tickets[0]["category"])
    _add_resolved(store, tickets[1])

    results = store.find_similar(tickets[0]["ticket_text"], k=5)

    returned_ids = {case.id for case, _, _, _ in results}
    assert open_case.id not in returned_ids


# --------------------------------------------------------------------------
# find_similar — фолбэк на FTS5, когда модель эмбеддингов недоступна
# --------------------------------------------------------------------------


def test_find_similar_falls_back_to_fts_when_model_unavailable(store, unavailable_model, tickets):
    cases = [_add_resolved(store, t) for t in tickets[:6]]

    # слово "рюкзака" встречается только в tickets[3] (дюп рюкзаков у Groza_91)
    results = store.find_similar("рюкзака", k=5)

    assert results, "FTS должен найти релевантный кейс по ключевому слову"
    methods = {method for _, _, _, method in results}
    assert methods == {"fts"}
    matched_ids = {case.id for case, _, _, _ in results}
    assert cases[3].id in matched_ids


def test_find_similar_fts_category_filter(store, unavailable_model, tickets):
    cheaty_case = _add_resolved(store, tickets[3])  # читы, уникальное слово "рюкзака"
    _add_resolved(store, tickets[4])  # потеря лута

    results_wrong_category = store.find_similar("рюкзака", k=5, category="потеря лута")
    assert results_wrong_category == []

    results_ok = store.find_similar("рюкзака", k=5, category="читы")
    matched_ids = {case.id for case, _, _, _ in results_ok}
    assert cheaty_case.id in matched_ids
    assert all(method == "fts" for _, _, _, method in results_ok)


def test_find_similar_fts_no_results_for_unmatched_query(store, unavailable_model, tickets):
    _add_resolved(store, tickets[0])

    results = store.find_similar("совершенно_несвязанное_слово_зщхъ", k=5)

    assert results == []


def test_find_similar_fts_empty_query_does_not_crash(store, unavailable_model, tickets):
    _add_resolved(store, tickets[0])

    results = store.find_similar("", k=5)

    assert results == []
