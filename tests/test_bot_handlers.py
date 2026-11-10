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
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.ai.schemas import AdviceResponse, TicketExtraction
from app.bot import handlers
from app.cases import CaseStore
from app.cases import search as cases_search
from app.logs.base import LogEvent
from app.logs.sqlite_source import SqliteLogSource

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


def test_analyze_ticket_with_sqlite_log_source_surfaces_car_opener_and_nearby_player(store):
    """E2E: тикет «пропал лут из машины CrSk_UAZ_469» с настоящим SqliteLogSource
    (вместо NullLogSource) -- analyze_ticket должен прогнать build_log_facts через него,
    и итоговый LogFacts, переданный в advise(), должен содержать и историю машины, и
    игрока рядом с ней в окно офлайна владельца, по данным мини-индекса.

    НАЙДЕН ДЕФЕКТ-В-ТЕСТЕ (не в коде): до этой правки тест проверял старые поля
    LogFacts.vehicle/events/nearby_players, которые после разделения build_log_facts
    по категории тикета (commit 9ba8595/4d4a9e5) для category="loot_loss" больше не
    заполняются -- заполняются vehicle_history/others_access/offline_nearby/owner_actions
    (см. app/logs/facts.py:_build_loot_loss_facts). Тест был не обновлён и всегда проходил
    "случайно" (все старые поля пустые, но и явных assert на их пустоту не было -- кроме
    assert на непустой .vehicle/.events, который теперь падает). Ниже -- актуальный контракт.

    `now` здесь -- naive локальное время (SERVER_TZ), как и весь app/logs/facts.py после
    фикса сдвига с UTC (см. докстринг модуля): aware datetime.now(timezone.utc) не годится,
    т.к. resolve_time_window() центрирует окно ±2ч вокруг naive datetime.now() и на этом
    сервере (MSK, UTC+3) события с aware UTC-таймстампом вылетают за пределы окна."""
    now = datetime.now()

    def fake_extract_ticket(text, *, client=None):
        return TicketExtraction(
            player_name=None, player_id=None, category="loot_loss",
            summary="Пропал лут из машины CrSk_UAZ_469", time_hint=None, needs_logs=True,
        )

    captured_log_facts = {}

    def fake_advise(ticket, similar_cases, log_facts, *, client=None):
        captured_log_facts["value"] = log_facts
        return AdviceResponse(
            summary="Разбор угона машины", facts=["см. log_facts"], similar=[],
            recommendation="Проверить вручную", confidence=0.5, open_questions=[],
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(handlers, "extract_ticket", fake_extract_ticket)
        mp.setattr(handlers, "advise", fake_advise)

        source = SqliteLogSource(":memory:")
        try:
            # Идентичность владельца машины (как даёт реальный ADM connect/disconnect).
            source._index.upsert_identity(now - timedelta(minutes=30), name="TerezVds", bi_hash="HASH_TZ")
            source._index.upsert_identity(now - timedelta(minutes=29), name="TerezVds", steam64=76561199000000031)

            open_ts = now - timedelta(minutes=10)
            close_ts = now - timedelta(minutes=5)
            source._index.add_event(
                LogEvent(
                    ts=open_ts, kind="vehicle_open", player=None, player_id=76561199000000031,
                    text="open transport CrSk_UAZ_469", raw=None, steam64=76561199000000031,
                    x=100.0, z=200.0, extra={"vehicle_class": "CrSk_UAZ_469"},
                ),
                source="carcodelock",
            )
            source._index.add_event(
                LogEvent(
                    ts=close_ts, kind="vehicle_close", player=None, player_id=76561199000000031,
                    text="close transport CrSk_UAZ_469", raw=None, steam64=76561199000000031,
                    x=101.0, z=201.0, extra={"vehicle_class": "CrSk_UAZ_469"},
                ),
                source="carcodelock",
            )
            # Кто-то стоял рядом с машиной в момент, близкий к закрытию (roster-снимок ZF).
            source._index.add_event(
                LogEvent(
                    ts=close_ts - timedelta(minutes=1), kind="roster", player="Spectator1", player_id=None,
                    text="roster", raw=None, steam64=900000000000000002, x=108.0, z=205.0,
                    extra={"pos": (108.0, 0.0, 205.0), "playtime": "0 д."},
                ),
                source="zf",
            )
            # Контроль: игрок далеко от машины не должен попасть в "рядом".
            source._index.add_event(
                LogEvent(
                    ts=close_ts, kind="roster", player="FarAway", player_id=None,
                    text="roster", raw=None, steam64=900000000000000003, x=9000.0, z=9000.0,
                    extra={"pos": (9000.0, 0.0, 9000.0), "playtime": "0 д."},
                ),
                source="zf",
            )
            # Окно офлайна владельца, бракетирующее close_ts -- без него offline_nearby
            # (единственное место, где для loot_loss используется nearby_players) не
            # запрашивается вовсе, и Spectator1 никак не попал бы в LogFacts.
            source._index.add_event(
                LogEvent(
                    ts=close_ts - timedelta(minutes=2), kind="disconnect", player="TerezVds",
                    player_id=76561199000000031, text="disconnected", raw=None, steam64=76561199000000031,
                ),
                source="adm",
            )
            source._index.add_event(
                LogEvent(
                    ts=close_ts + timedelta(minutes=2), kind="connect", player="TerezVds",
                    player_id=None, text="connected", raw=None, bi_hash="HASH_TZ",
                ),
                source="adm",
            )
            source._index.commit()

            ticket = handlers.Ticket(
                text="У меня пропал лут из машины CrSk_UAZ_469",
                player_name="TerezVds",
            )
            result = handlers.analyze_ticket(store, ticket, log_source=source)
        finally:
            source.close()

    log_facts = captured_log_facts["value"]
    assert log_facts.target_class == "CrSk_UAZ_469"
    assert any("CrSk_UAZ_469" in line for line in log_facts.vehicle_history)
    assert log_facts.others_access == []  # оба события -- действия самого владельца
    assert any("Spectator1" in line for line in log_facts.offline_nearby)
    assert all("FarAway" not in line for line in log_facts.offline_nearby)
    assert any("CrSk_UAZ_469" in line for line in log_facts.owner_actions)
    assert result.advice.summary == "Разбор угона машины"


def _loot_loss_fake_extract_ticket(text, *, client=None):
    return TicketExtraction(
        player_name=None, player_id=None, category="loot_loss",
        summary="У меня пропал весь лут из машины Bastard_cj7_green", time_hint=None, needs_logs=True,
    )


def _capture_advise(captured: dict):
    def fake_advise(ticket, similar_cases, log_facts, *, client=None):
        captured["value"] = log_facts
        return AdviceResponse(
            summary="Разбор пропажи лута", facts=["см. log_facts"], similar=[],
            recommendation="Проверить вручную", confidence=0.5, open_questions=[],
        )

    return fake_advise


def test_analyze_ticket_loot_loss_vehicle_with_garage_history_and_no_outside_access(store):
    """Главный e2e-тест facts.py (миссия 2.1): тикет категории loot_loss про
    конкретную машину (Bastard_cj7_green) с историей гаража владельца и без
    единого обращения посторонних -- build_log_facts должен найти историю
    машины (vehicle_history) и явно сказать, что посторонних не было."""
    now = datetime.now()
    captured_log_facts: dict = {}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(handlers, "extract_ticket", _loot_loss_fake_extract_ticket)
        mp.setattr(handlers, "advise", _capture_advise(captured_log_facts))

        source = SqliteLogSource(":memory:")
        try:
            owner_steam64 = 900000000000000010
            source._index.add_event(
                LogEvent(
                    ts=now - timedelta(minutes=40), kind="garage_store", player="Boris",
                    player_id=owner_steam64, text="Vehicle Stored", raw=None, steam64=owner_steam64,
                    extra={
                        "vehicle_class": "Bastard_cj7_green", "trader_id": 0,
                        "pos": "500.0, 600.0, 10.0", "had_insurance": False, "insurance_value": 0,
                    },
                ),
                source="lbmaster",
            )
            source._index.add_event(
                LogEvent(
                    ts=now - timedelta(minutes=30), kind="garage_restore", player="Boris",
                    player_id=owner_steam64, text="Vehicle Restored", raw=None, steam64=owner_steam64,
                    extra={
                        "vehicle_class": "Bastard_cj7_green", "trader_id": 0, "vehicle_uid": 555111,
                        "pos": "500.0, 600.0, 10.0", "in_garage_for": "10m 0s",
                        "infinite": False, "insurance": False, "value": 0,
                    },
                ),
                source="lbmaster",
            )
            source._index.commit()

            ticket = handlers.Ticket(text="У меня пропал весь лут из машины Bastard_cj7_green", player_name="Boris")
            handlers.analyze_ticket(store, ticket, log_source=source)
        finally:
            source.close()

    log_facts = captured_log_facts["value"]
    assert log_facts.target_class == "Bastard_cj7_green"
    assert log_facts.vehicle_history  # история гаража владельца найдена
    assert any("garage" in line or "Vehicle" in line for line in log_facts.vehicle_history)
    assert log_facts.others_access == []

    prompt_text = log_facts.to_prompt_text()
    assert "История транспорта/контейнера (Bastard_cj7_green):" in prompt_text
    assert "Других игроков, трогавших Bastard_cj7_green, в логах нет." in prompt_text
    assert "Кто кроме владельца" not in prompt_text


def test_analyze_ticket_loot_loss_vehicle_access_by_other_player_is_surfaced(store):
    """Тот же сценарий, но с посторонним обращением к машине (Thief взял предмет
    из Bastard_cj7_green) -- others_access должен быть непустым, а итоговый
    текст должен показывать раздел "кто кроме владельца брал", а не строку
    "в логах нет"."""
    now = datetime.now()
    captured_log_facts: dict = {}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(handlers, "extract_ticket", _loot_loss_fake_extract_ticket)
        mp.setattr(handlers, "advise", _capture_advise(captured_log_facts))

        source = SqliteLogSource(":memory:")
        try:
            owner_steam64 = 900000000000000010
            thief_steam64 = 900000000000000099
            source._index.add_event(
                LogEvent(
                    ts=now - timedelta(minutes=40), kind="garage_store", player="Boris",
                    player_id=owner_steam64, text="Vehicle Stored", raw=None, steam64=owner_steam64,
                    extra={
                        "vehicle_class": "Bastard_cj7_green", "trader_id": 0,
                        "pos": "500.0, 600.0, 10.0", "had_insurance": False, "insurance_value": 0,
                    },
                ),
                source="lbmaster",
            )
            source._index.add_event(
                LogEvent(
                    ts=now - timedelta(minutes=30), kind="garage_restore", player="Boris",
                    player_id=owner_steam64, text="Vehicle Restored", raw=None, steam64=owner_steam64,
                    extra={
                        "vehicle_class": "Bastard_cj7_green", "trader_id": 0, "vehicle_uid": 555111,
                        "pos": "500.0, 600.0, 10.0", "in_garage_for": "10m 0s",
                        "infinite": False, "insurance": False, "value": 0,
                    },
                ),
                source="lbmaster",
            )
            # Как реальный zf.py: text -- это verb_rest, имя субъекта в него не входит
            # (см. находку test_event_line_mentions_actor_even_when_parser_text_omits_the_name).
            source._index.add_event(
                LogEvent(
                    ts=now - timedelta(minutes=35), kind="item_action", player="Thief",
                    player_id=thief_steam64, text="взял с расстояния 2 м AKM (1) (ID: 321) из Транспортное средство (Bastard_cj7_green)",
                    raw=None, steam64=thief_steam64,
                    extra={
                        "verb": "взял с расстояния", "item": "AKM", "item_class": "AKM",
                        "qty": 1, "item_id": "321", "distance": 2.0,
                        "src_chain": ["Транспортное средство (Bastard_cj7_green)"],
                        "source_chain": ["Транспортное средство (Bastard_cj7_green)"],
                        "src_root_class": "Bastard_cj7_green",
                    },
                ),
                source="zf",
            )
            source._index.commit()

            ticket = handlers.Ticket(text="У меня пропал весь лут из машины Bastard_cj7_green", player_name="Boris")
            handlers.analyze_ticket(store, ticket, log_source=source)
        finally:
            source.close()

    log_facts = captured_log_facts["value"]
    assert log_facts.target_class == "Bastard_cj7_green"
    assert len(log_facts.others_access) == 1

    prompt_text = log_facts.to_prompt_text()
    assert "Кто кроме владельца открывал/брал/клал:" in prompt_text
    assert "Других игроков, трогавших Bastard_cj7_green, в логах нет." not in prompt_text


@pytest.mark.xfail(
    strict=True,
    reason=(
        "НАЙДЕН ДЕФЕКТ (app/logs/facts.py:_event_line), расширение уже известной находки "
        "(test_event_line_mentions_actor_even_when_parser_text_omits_the_name в test_logs_facts.py) "
        "на главный сценарий loot_loss: строка в others_access строится из event.text, который у "
        "реального zf.py НЕ содержит имя субъекта (см. zf.py:_build_item_event, text=full_text="
        "verb_rest без имени) -- итоговый 'Кто кроме владельца открывал/брал/клал' не называет, КТО "
        "именно это сделал (Thief), хотя event.player у этого события заполнен."
    ),
)
def test_others_access_line_names_the_other_player_who_took_the_item(store):
    now = datetime.now()
    captured_log_facts: dict = {}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(handlers, "extract_ticket", _loot_loss_fake_extract_ticket)
        mp.setattr(handlers, "advise", _capture_advise(captured_log_facts))

        source = SqliteLogSource(":memory:")
        try:
            owner_steam64 = 900000000000000010
            thief_steam64 = 900000000000000099
            source._index.add_event(
                LogEvent(
                    ts=now - timedelta(minutes=40), kind="garage_store", player="Boris",
                    player_id=owner_steam64, text="Vehicle Stored", raw=None, steam64=owner_steam64,
                    extra={
                        "vehicle_class": "Bastard_cj7_green", "trader_id": 0,
                        "pos": "500.0, 600.0, 10.0", "had_insurance": False, "insurance_value": 0,
                    },
                ),
                source="lbmaster",
            )
            source._index.add_event(
                LogEvent(
                    ts=now - timedelta(minutes=35), kind="item_action", player="Thief",
                    player_id=thief_steam64, text="взял с расстояния 2 м AKM (1) (ID: 321) из Транспортное средство (Bastard_cj7_green)",
                    raw=None, steam64=thief_steam64,
                    extra={
                        "verb": "взял с расстояния", "item": "AKM", "item_class": "AKM",
                        "qty": 1, "item_id": "321", "distance": 2.0,
                        "src_chain": ["Транспортное средство (Bastard_cj7_green)"],
                        "source_chain": ["Транспортное средство (Bastard_cj7_green)"],
                        "src_root_class": "Bastard_cj7_green",
                    },
                ),
                source="zf",
            )
            source._index.commit()

            ticket = handlers.Ticket(text="У меня пропал весь лут из машины Bastard_cj7_green", player_name="Boris")
            handlers.analyze_ticket(store, ticket, log_source=source)
        finally:
            source.close()

    log_facts = captured_log_facts["value"]
    assert any("Thief" in line for line in log_facts.others_access)


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
