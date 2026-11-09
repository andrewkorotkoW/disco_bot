"""E2E тест pipeline app.bot.handlers без сети и без discord gateway.

app.cases используется реальным (SQLite на tmp_path), но эмбеддер
(app.cases.search.embed_text) подменён на детерминированный bag-of-words,
чтобы не тянуть sentence-transformers и не зависеть от сети/весов модели.
app.ai (extract_ticket/advise), как использует их handlers.py, подменён
фейками — реальный Claude не вызывается.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from app.ai.schemas import AdviceResponse, TicketExtraction
from app.bot import handlers
from app.cases import CaseStore
from app.cases import search as cases_search

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "tickets.json"
VECTOR_SIZE = 64


def load_fixture_tickets() -> list[dict]:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


def fake_embed_text(text: str) -> list[float]:
    """Детерминированный bag-of-words эмбеддер: без сети, без весов модели."""
    vector = [0.0] * VECTOR_SIZE
    for token in re.findall(r"\w+", text.lower(), re.UNICODE):
        idx = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % VECTOR_SIZE
        vector[idx] += 1.0
    return vector


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(cases_search, "embed_text", fake_embed_text)
    case_store = CaseStore(str(tmp_path / "cases.db"))
    yield case_store
    case_store.close()


@pytest.fixture
def ai_calls(monkeypatch):
    """Подменяет handlers.extract_ticket/handlers.advise и записывает вызовы."""
    calls = {"extract": [], "advise": []}

    def fake_extract_ticket(text, *, client=None):
        calls["extract"].append(text)
        return TicketExtraction(
            player_name=None,
            player_id=None,
            category="cheat",
            summary=f"Авторазбор: {text[:40]}",
            time_hint=None,
            needs_logs=True,
        )

    def fake_advise(ticket, similar_cases, log_facts, *, client=None):
        calls["advise"].append(
            {"ticket": ticket, "similar_cases": similar_cases, "log_facts": log_facts}
        )
        return AdviceResponse(
            summary="Тестовый разбор",
            facts=["логи не предоставлены"] if not log_facts else ["есть логи"],
            similar=[f"case {case.id}" for case, *_ in similar_cases],
            recommendation="Проверить вручную",
            confidence=0.5,
            open_questions=[],
        )

    monkeypatch.setattr(handlers, "extract_ticket", fake_extract_ticket)
    monkeypatch.setattr(handlers, "advise", fake_advise)
    return calls


WALLHACK_TICKET = (
    "Залетел на базу к SnezhniyBars, а он через стену сквозь бетон меня застрелил "
    "из AKM, не видя меня вообще. Спидхак точно, он телепортировался за полсекунды "
    "через всю базу."
)
WALLHACK_TICKET_NEAR_DUPLICATE = (
    "Снова на базу к SnezhniyBars залетели такие же читеры — через стену и бетон "
    "стреляют, не видя игрока. Похоже на тот же спидхак с телепортом через всю базу."
)
LOOT_LOSS_TICKET = (
    "После вылета сервера вчера в 23:40 у меня пропал весь рюкзак с оружием, который "
    "я держала в руках. Зашла — инвентарь пустой, а база цела."
)


def test_analyze_ticket_new_case_has_no_similar_yet(store, ai_calls):
    ticket = handlers.Ticket(
        text=WALLHACK_TICKET,
        source_channel_id=1,
        source_message_id=101,
        player_name="Dima_Craft",
        player_id=900000000000000001,
    )

    result = handlers.analyze_ticket(store, ticket)

    assert result.case.id is not None
    assert result.case.status == "open"
    assert result.case.ticket_text == WALLHACK_TICKET
    assert result.extraction.summary.startswith("Авторазбор:")
    assert result.similar == []  # ничего ещё не resolved — эмбеддингов в базе нет
    assert result.advice.summary == "Тестовый разбор"

    # extract_ticket вызван с текстом тикета, advise получил именно эту extraction и [] похожих
    assert ai_calls["extract"] == [WALLHACK_TICKET]
    assert ai_calls["advise"][0]["ticket"] is result.extraction
    assert ai_calls["advise"][0]["similar_cases"] == []
    assert ai_calls["advise"][0]["log_facts"] == []  # NullLogSource


def test_resolve_then_similar_ticket_finds_resolved_case_with_decision(store, ai_calls):
    wallhack_ticket = handlers.Ticket(text=WALLHACK_TICKET, player_name="Dima_Craft")
    wallhack_result = handlers.analyze_ticket(store, wallhack_ticket)

    loot_ticket = handlers.Ticket(text=LOOT_LOSS_TICKET, player_name="OlgaFaraday")
    loot_result = handlers.analyze_ticket(store, loot_ticket)

    wallhack_decision_text = "Подтверждено по демо — вскрытие стен. Перманентный бан."
    loot_decision_text = "Вылет подтверждён логами, лут не восстанавливаем (правило сервера)."

    decision = handlers.resolve_ticket_case(
        store, wallhack_result.case.id, admin_id=800000000000000001, decision_text=wallhack_decision_text
    )
    handlers.resolve_ticket_case(
        store, loot_result.case.id, admin_id=800000000000000002, decision_text=loot_decision_text
    )

    assert decision.case_id == wallhack_result.case.id
    assert decision.decision_text == wallhack_decision_text

    # Новый похожий тикет -> через search_similar (как /similar, без Claude) находит именно
    # resolved-кейс про вскрытие стен, а не про потерю лута, вместе с его решением.
    similar = handlers.search_similar(store, WALLHACK_TICKET_NEAR_DUPLICATE)

    assert len(similar) == 2
    top_case, top_decision, top_score, top_method = similar[0]
    assert top_case.id == wallhack_result.case.id
    assert top_decision is not None
    assert top_decision.decision_text == wallhack_decision_text
    assert top_method == "embedding"

    other_case, _, other_score, _ = similar[1]
    assert other_case.id == loot_result.case.id
    assert top_score > other_score  # вскрытие стен ближе по словам, чем потеря лута

    # И полный pipeline analyze_ticket для нового тикета видит тот же resolved-кейс в similar
    # (а не самого себя) и передаёт его в advise.
    new_wallhack_ticket = handlers.Ticket(text=WALLHACK_TICKET_NEAR_DUPLICATE)
    new_result = handlers.analyze_ticket(store, new_wallhack_ticket)

    assert any(case.id == wallhack_result.case.id for case, *_ in new_result.similar)
    assert all(case.id != new_result.case.id for case, *_ in new_result.similar)
    passed_similar = ai_calls["advise"][-1]["similar_cases"]
    assert any(case.id == wallhack_result.case.id for case, *_ in passed_similar)


def test_get_case_detail_returns_decision_after_resolve(store, ai_calls):
    ticket = handlers.Ticket(text=WALLHACK_TICKET)
    result = handlers.analyze_ticket(store, ticket)

    detail_before = handlers.get_case_detail(store, result.case.id)
    assert detail_before.case.id == result.case.id
    assert detail_before.decision is None

    decision_text = "Бан на 30 дней за вскрытие стен."
    handlers.resolve_ticket_case(store, result.case.id, admin_id=1, decision_text=decision_text)

    detail_after = handlers.get_case_detail(store, result.case.id)
    assert detail_after.decision is not None
    assert detail_after.decision.decision_text == decision_text
    assert detail_after.case.status == "resolved"


def test_get_case_detail_unknown_case_returns_none(store):
    assert handlers.get_case_detail(store, 999999) is None


def test_resolve_ticket_case_unknown_case_raises(store):
    with pytest.raises(ValueError):
        handlers.resolve_ticket_case(store, 999999, admin_id=1, decision_text="x")


def test_find_similar_falls_back_to_fts_when_embedder_unavailable(store, ai_calls, monkeypatch):
    ticket = handlers.Ticket(text=WALLHACK_TICKET)
    result = handlers.analyze_ticket(store, ticket)
    handlers.resolve_ticket_case(
        store, result.case.id, admin_id=1, decision_text="Перманентный бан за вскрытие стен."
    )

    def boom(_text):
        raise RuntimeError("модель эмбеддингов недоступна (нет сети)")

    monkeypatch.setattr(cases_search, "embed_text", boom)

    similar = handlers.search_similar(store, WALLHACK_TICKET_NEAR_DUPLICATE)

    assert len(similar) == 1
    case, decision, _score, method = similar[0]
    assert case.id == result.case.id
    assert method == "fts"
    assert decision is not None


def test_fixture_tickets_load_and_round_trip_through_store(store):
    """Фикстура tickets.json грузится и каждый тикет можно добавить/resolve в CaseStore."""
    fixture_tickets = load_fixture_tickets()
    assert len(fixture_tickets) == 20

    for entry in fixture_tickets[:3]:
        case = store.add_case(
            entry["ticket_text"],
            source_channel_id=entry["source_channel_id"],
            source_message_id=entry["source_message_id"],
            player_name=entry["player_name"],
            player_id=entry["player_id"],
        )
        decision = handlers.resolve_ticket_case(
            store,
            case.id,
            admin_id=entry["decision"]["admin_id"],
            decision_text=entry["decision"]["decision_text"],
        )
        assert decision.decision_text == entry["decision"]["decision_text"]
