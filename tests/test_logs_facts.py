"""Тесты build_log_facts/resolve_time_window (app.logs.facts): агрегация повторов,
лимит MAX_EVENTS, needs_logs=False не трогает источник, time_hint тикета."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ai.schemas import TicketExtraction
from app.logs.base import LogEvent
from app.logs.facts import MAX_EVENTS, LogFacts, build_log_facts, resolve_time_window
from app.logs.identity import Identity

T0 = datetime(2026, 11, 9, 4, 0, 0)


def _t(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _extraction(**overrides) -> TicketExtraction:
    base = dict(
        player_name="Victim", player_id=None, category="loot_loss",
        summary="пропал лут", time_hint=None, needs_logs=True,
    )
    base.update(overrides)
    return TicketExtraction(**base)


class FakeSource:
    """Минимальная реализация LogSource + опциональных методов, которые
    build_log_facts достаёт через getattr(..., None)."""

    def __init__(self, events=None, offline_windows=None, nearby_players=None, identity=None):
        self._events = events or []
        self._offline_windows = offline_windows or []
        self._nearby_players = nearby_players or []
        self._identity = identity
        self.query_calls = []
        self.offline_windows_calls = []
        self.nearby_players_calls = []
        self.resolve_calls = []

    def query(self, player=None, since=None, until=None, kinds=None):
        self.query_calls.append({"player": player, "since": since, "until": until, "kinds": kinds})
        return list(self._events)

    def offline_windows(self, player):
        self.offline_windows_calls.append(player)
        return list(self._offline_windows)

    def nearby_players(self, x, z, radius, at, window_minutes=10):
        self.nearby_players_calls.append((x, z, radius, at))
        return list(self._nearby_players)

    def resolve(self, key):
        self.resolve_calls.append(key)
        if self._identity is None:
            return Identity()
        return self._identity


def _hit_event(ts, label="infected") -> LogEvent:
    return LogEvent(
        ts=ts, kind="hit", player="Victim", player_id=None, text="hit", raw=None,
        extra={"subtype": label},
    )


class RaisingSource:
    """Источник, который должен никогда не быть вызван (needs_logs=False)."""

    def query(self, player=None, since=None, until=None, kinds=None):
        raise AssertionError("source.query не должен вызываться при needs_logs=False")


# --- needs_logs ---------------------------------------------------------------


def test_needs_logs_false_returns_empty_facts_without_touching_source():
    extraction = _extraction(needs_logs=False)

    facts = build_log_facts(extraction, RaisingSource())

    assert facts == LogFacts()
    assert not facts
    assert facts == []  # обратная совместимость со старым контрактом (пустой список)


def test_needs_logs_true_queries_source_with_player_key_since_until():
    source = FakeSource()
    since, until = _t(0), _t(100)

    build_log_facts(_extraction(player_name="Victim"), source, since=since, until=until)

    assert source.query_calls == [{"player": "Victim", "since": since, "until": until, "kinds": None}]


def test_player_key_falls_back_to_player_id_when_name_missing():
    source = FakeSource()

    build_log_facts(_extraction(player_name=None, player_id="76561199000000099"), source)

    assert source.query_calls[0]["player"] == "76561199000000099"


# --- агрегация/лимит -----------------------------------------------------------
#
# НАЙДЕНО (тест, не код): _extraction() по умолчанию ставит category="loot_loss",
# что до разделения build_log_facts по категории (commit 9ba8595/4d4a9e5) было не
# важно, а теперь уводит в _build_loot_loss_facts, где .events/.offline_windows/
# .nearby_players/.vehicle не заполняются вовсе (это поля _build_timeline_facts).
# Ниже -- тесты именно общего таймлайна, им нужна category из ("bug","other",
# "ban_appeal"), иначе они проверяют пустые списки "случайно верно".


def test_events_at_or_below_limit_are_rendered_one_line_each_even_if_repeated():
    events = [_hit_event(_t(i)) for i in range(5)]  # все одного типа, но 5 <= MAX_EVENTS
    source = FakeSource(events=events)

    facts = build_log_facts(_extraction(category="bug"), source)

    assert len(facts.events) == 5


def test_events_above_limit_aggregate_repeated_kind_label_into_counted_line():
    n = MAX_EVENTS + 5
    events = [_hit_event(_t(i)) for i in range(n)]
    source = FakeSource(events=events)

    facts = build_log_facts(_extraction(category="bug"), source)

    assert len(facts.events) == 1
    assert f"×{n}" in facts.events[0]
    assert "урон от зомби" in facts.events[0]


def test_events_above_limit_keeps_distinct_kinds_separate_and_respects_limit():
    zombie_hits = [_hit_event(_t(i), label="infected") for i in range(MAX_EVENTS + 1)]
    other = LogEvent(ts=_t(9999), kind="chat", player="Victim", player_id=None, text="привет", raw=None)
    source = FakeSource(events=zombie_hits + [other])

    facts = build_log_facts(_extraction(category="bug"), source)

    assert len(facts.events) <= MAX_EVENTS
    assert any("×" in line for line in facts.events)
    assert any("привет" in line for line in facts.events)


# --- offline_windows / nearby_players / vehicle / related ---------------------


def test_offline_windows_are_formatted_into_facts():
    source = FakeSource(offline_windows=[(_t(0), _t(60))])

    facts = build_log_facts(_extraction(category="bug"), source)

    assert len(facts.offline_windows) == 1
    assert "offline" in facts.offline_windows[0]
    assert source.offline_windows_calls == ["Victim"]


def test_nearby_players_use_latest_known_position_from_events():
    events = [
        LogEvent(ts=_t(0), kind="roster", player="Victim", player_id=None, text="r", raw=None, x=1.0, z=2.0),
        LogEvent(ts=_t(10), kind="roster", player="Victim", player_id=None, text="r", raw=None, x=3.0, z=4.0),
    ]
    source = FakeSource(events=events, nearby_players=[{"player": "Suspect", "distance": 5.0, "ts": _t(10)}])

    facts = build_log_facts(_extraction(category="bug"), source)

    assert source.nearby_players_calls == [(3.0, 4.0, 50.0, _t(10))]  # самая свежая позиция, не первая
    assert any("Suspect" in line for line in facts.nearby_players)


def test_no_nearby_players_call_when_no_position_known():
    source = FakeSource(events=[LogEvent(ts=_t(0), kind="chat", player="Victim", player_id=None, text="hi", raw=None)])

    facts = build_log_facts(_extraction(category="bug"), source)

    assert source.nearby_players_calls == []
    assert facts.nearby_players == []


def test_vehicle_field_aggregates_vehicle_classes_with_counts():
    events = [
        LogEvent(ts=_t(0), kind="vehicle_open", player="Victim", player_id=None, text="o", raw=None, extra={"vehicle_class": "CrSk_UAZ_469"}),
        LogEvent(ts=_t(5), kind="vehicle_close", player="Victim", player_id=None, text="c", raw=None, extra={"vehicle_class": "CrSk_UAZ_469"}),
    ]
    source = FakeSource(events=events)

    facts = build_log_facts(_extraction(category="bug"), source)

    assert facts.vehicle == "CrSk_UAZ_469 (×2)"


def test_related_identities_exclude_self_name_but_include_other_names_and_steam64():
    identity = Identity(names={"Victim", "OldNick"}, steam64s={111}, steam64=111)
    source = FakeSource(identity=identity)

    facts = build_log_facts(_extraction(player_name="Victim"), source)

    assert "ник: OldNick" in facts.related
    assert "ник: Victim" not in facts.related
    assert "steam64: 111" in facts.related


# --- resolve_time_window -------------------------------------------------------


def test_resolve_time_window_centers_on_parseable_time_hint():
    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    since, until = resolve_time_window("2026-11-09 23:40:00", now=now)

    # time_hint без явного offset -> _parse_time_hint возвращает naive datetime
    # (единое соглашение о naive местном времени для всего app/logs, см. докстринг
    # facts.py) независимо от того, aware ли был передан "now".
    center = datetime(2026, 11, 9, 23, 40)
    assert since == center - timedelta(hours=2)
    assert until == center + timedelta(hours=2)


def test_resolve_time_window_falls_back_to_now_when_hint_is_unparseable_natural_language():
    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    since, until = resolve_time_window("вчера вечером", now=now)

    assert since == now - timedelta(hours=2)
    assert until == now + timedelta(hours=2)


def test_resolve_time_window_falls_back_to_now_when_hint_missing():
    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    since, until = resolve_time_window(None, now=now)

    assert since == now - timedelta(hours=2)
    assert until == now + timedelta(hours=2)


# --- находки (см. финальный отчёт) --------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "НАЙДЕН ДЕФЕКТ (app/logs/facts.py:build_log_facts): nearby_players() вызывается с "
        "последней известной позицией самого игрока, и сам игрок (его же roster-снимок в той "
        "же точке) не отфильтровывается из результата -- 'кто рядом' может указывать на самого "
        "автора тикета, что сбивает с толку при разборе."
    ),
)
def test_nearby_players_fact_excludes_the_ticket_subject_themselves():
    # category="bug" -- иначе (см. category-split, commit 9ba8595) build_log_facts
    # уходит в _build_loot_loss_facts, которая вообще не вызывает nearby_players(),
    # и тест "проходит" вслепую (facts.nearby_players всегда [] по другой причине),
    # маскируя сам дефект вместо того, чтобы его демонстрировать.
    events = [LogEvent(ts=_t(0), kind="roster", player="Victim", player_id=None, text="r", raw=None, x=1.0, z=2.0)]
    source = FakeSource(
        events=events,
        nearby_players=[
            {"player": "Victim", "distance": 0.0, "ts": _t(0)},
            {"player": "Suspect", "distance": 5.0, "ts": _t(0)},
        ],
    )

    facts = build_log_facts(_extraction(player_name="Victim", category="bug"), source)

    assert not any("рядом игрок Victim " in line for line in facts.nearby_players)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "НАЙДЕН ДЕФЕКТ (app/logs/facts.py:_event_line): строка факта для события не включает "
        "игрока отдельно от event.text, а у ZF/CarCodeLock (главные источники по лутy/угону "
        "машин, см. docs/LOGS.md) парсеры не кладут имя/steam64 игрока в text -- итоговая "
        "строка факта о vehicle_open/item_action не говорит, КТО совершил действие."
    ),
)
def test_event_line_mentions_actor_even_when_parser_text_omits_the_name():
    # Как реальный carcodelock.py: player=None, steam64 заполнен, но text не содержит ни того, ни другого.
    event = LogEvent(
        ts=_t(0), kind="vehicle_open", player=None, player_id=76561199000000041, text="open transport CrSk_UAZ_469",
        raw=None, steam64=76561199000000041, extra={"vehicle_class": "CrSk_UAZ_469"},
    )
    source = FakeSource(events=[event])

    # category="bug" -- иначе facts.events всегда [] (поле _build_timeline_facts),
    # и assert ниже падал бы по категории, а не по реальному дефекту _event_line.
    facts = build_log_facts(_extraction(player_name="76561199000000041", category="bug"), source)

    assert any("76561199000000041" in line for line in facts.events)
