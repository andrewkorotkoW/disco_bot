"""Тесты SqliteLogSource: nearby_players, offline_windows, container_access.

События вставляются напрямую через LogIndex.add_event() с extra в той же форме,
что реально кладут парсеры (zf.py/adm.py), чтобы не зависеть от деталей regexp-ов,
но точно воспроизводить структуру source_chain/dest_chain и roster-снимков."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.logs.base import LogEvent
from app.logs.sqlite_source import SqliteLogSource

T0 = datetime(2026, 11, 9, 4, 0, 0)


def _t(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


@pytest.fixture
def source():
    src = SqliteLogSource(":memory:")
    yield src
    src.close()


def _roster_event(ts: datetime, player: str, steam64: int, x: float, z: float) -> LogEvent:
    return LogEvent(
        ts=ts, kind="roster", player=player, player_id=steam64, text=f"roster {player}", raw=None,
        steam64=steam64, x=x, z=z, extra={"pos": (x, 0.0, z), "playtime": "0 д."},
    )


# --- nearby_players ---------------------------------------------------------


def test_nearby_players_finds_player_within_radius_and_window(source):
    # nearby_players -- чисто пространственный запрос без привязки к "своему" игроку: если
    # в запрашиваемой точке есть roster-снимок самого Victim (distance=0), он тоже попадёт
    # в результат вместе с Suspect -- фильтрация "это не я" делается (или не делается) выше,
    # на уровне вызывающего кода (см. app/logs/facts.py).
    source._index.add_event(_roster_event(_t(0), "Victim", 1, x=100.0, z=100.0), source="zf")
    source._index.add_event(_roster_event(_t(2), "Suspect", 2, x=110.0, z=105.0), source="zf")
    source._index.commit()

    results = source.nearby_players(100.0, 100.0, radius=50.0, at=_t(0), window_minutes=10)

    assert [r["player"] for r in results] == ["Victim", "Suspect"]
    assert results[1]["steam64"] == 2


def test_nearby_players_excludes_player_outside_radius(source):
    source._index.add_event(_roster_event(_t(0), "FarAway", 3, x=5000.0, z=5000.0), source="zf")
    source._index.commit()

    results = source.nearby_players(100.0, 100.0, radius=50.0, at=_t(0), window_minutes=10)

    assert results == []


def test_nearby_players_excludes_player_outside_time_window(source):
    # Тот же игрок рядом по координатам, но roster-снимок сделан на час позже окна.
    source._index.add_event(_roster_event(_t(70), "LateSnapshot", 4, x=105.0, z=105.0), source="zf")
    source._index.commit()

    results = source.nearby_players(100.0, 100.0, radius=50.0, at=_t(0), window_minutes=10)

    assert results == []


def test_nearby_players_sorted_by_distance_ascending(source):
    source._index.add_event(_roster_event(_t(0), "Near", 5, x=110.0, z=100.0), source="zf")
    source._index.add_event(_roster_event(_t(0), "Mid", 6, x=130.0, z=100.0), source="zf")
    source._index.commit()

    results = source.nearby_players(100.0, 100.0, radius=50.0, at=_t(0), window_minutes=10)

    assert [r["player"] for r in results] == ["Near", "Mid"]


# --- offline_windows ---------------------------------------------------------


def test_offline_windows_pairs_disconnect_with_following_connect(source):
    source._index.add_event(
        LogEvent(ts=_t(0), kind="connect", player="Bob", player_id=None, text="connected", raw=None),
        source="adm",
    )
    source._index.add_event(
        LogEvent(ts=_t(10), kind="disconnect", player="Bob", player_id=None, text="disconnected", raw=None),
        source="adm",
    )
    source._index.add_event(
        LogEvent(ts=_t(25), kind="connect", player="Bob", player_id=None, text="connected", raw=None),
        source="adm",
    )
    source._index.commit()

    windows = source.offline_windows("Bob")

    assert windows == [(_t(10), _t(25))]


def test_offline_windows_open_ended_window_without_following_connect(source):
    source._index.add_event(
        LogEvent(ts=_t(10), kind="disconnect", player="Alice", player_id=None, text="disconnected", raw=None),
        source="adm",
    )
    source._index.commit()

    windows = source.offline_windows("Alice")

    assert windows == [(_t(10), None)]


def test_offline_windows_empty_for_player_with_no_disconnects(source):
    source._index.add_event(
        LogEvent(ts=_t(0), kind="connect", player="AlwaysOnline", player_id=None, text="connected", raw=None),
        source="adm",
    )
    source._index.commit()

    assert source.offline_windows("AlwaysOnline") == []


# --- container_access --------------------------------------------------------


def _item_action(ts, player, steam64, source_chain, dest_chain, item="SomeItem") -> LogEvent:
    extra = {"verb": "переместил", "item": item, "qty": 1, "item_id": "1"}
    if source_chain is not None:
        extra["source_chain"] = source_chain
    if dest_chain is not None:
        extra["dest_chain"] = dest_chain
    return LogEvent(
        ts=ts, kind="item_action", player=player, player_id=steam64, text=f"переместил {item}", raw=None,
        steam64=steam64, extra=extra,
    )


def test_container_access_matches_by_vehicle_id_in_source_chain(source):
    """Реальный кейс из docs/LOGS.md: Attack2Bag_Green переместили из машины на персонажа."""
    event = _item_action(
        _t(0), "[NAPA] Shama", 51,
        source_chain=["Транспортное средство (Bastard_cj7_green)", "Одежда (Attack2Bag_Green)"],
        dest_chain=["Персонаж", "Одежда (Attack2Bag_Green)"],
        item="Attack2Bag_Green",
    )
    source._index.add_event(event, source="zf")
    source._index.commit()

    matched = source.container_access("Bastard_cj7_green")

    assert len(matched) == 1
    assert matched[0].player == "[NAPA] Shama"


def test_container_access_excludes_events_not_touching_the_vehicle(source):
    vehicle_event = _item_action(
        _t(0), "Driver", 51,
        source_chain=["Транспортное средство (Bastard_cj7_green)"],
        dest_chain=["Персонаж"],
    )
    character_only_event = _item_action(
        _t(1), "Balabaster", 52,
        source_chain=["Персонаж", "Одежда (OtherBag)"],
        dest_chain=["Персонаж", "Одежда (Attack2Bag_Green)"],
    )
    source._index.add_event(vehicle_event, source="zf")
    source._index.add_event(character_only_event, source="zf")
    source._index.commit()

    matched = source.container_access("Bastard_cj7_green")

    assert [e.player for e in matched] == ["Driver"]


def test_container_access_distinguishes_character_root_from_vehicle_root(source):
    """'Персонаж -> Одежда (X)' и 'Транспортное средство -> Одежда (X)' должны различаться:
    запрос по корню 'Персонаж' не должен находить перемещение, которое целиком происходит
    внутри машины (оба конца пути -- 'Транспортное средство')."""
    character_move = _item_action(
        _t(0), "Balabaster", 52,
        source_chain=["Персонаж", "Одежда (OtherBag)"],
        dest_chain=["Персонаж", "Одежда (Attack2Bag_Green)"],
    )
    purely_in_vehicle_move = _item_action(
        _t(1), "Driver", 51,
        source_chain=["Транспортное средство (Bastard_cj7_green)", "Контейнер (Trunk)"],
        dest_chain=["Транспортное средство (Bastard_cj7_green)", "Одежда (SomeOtherBag)"],
    )
    source._index.add_event(character_move, source="zf")
    source._index.add_event(purely_in_vehicle_move, source="zf")
    source._index.commit()

    matched_character = source.container_access("Персонаж")
    assert [e.player for e in matched_character] == ["Balabaster"]

    matched_vehicle = source.container_access("Транспортное средство (Bastard_cj7_green)")
    assert [e.player for e in matched_vehicle] == ["Driver"]


def test_container_access_matches_nested_container_inside_vehicle(source):
    """Вложенный контейнер: сумка (Bag2) лежит в машине -- запрос по имени машины должен
    находить событие, даже когда сам контейнер назначения (Bag2) -- не корень пути."""
    nested_move = _item_action(
        _t(0), "Driver", 51,
        source_chain=["Транспортное средство (Bastard_cj7_green)", "Одежда (Bag2)"],
        dest_chain=["Персонаж", "Одежда (Bag2)"],
        item="Bag2Item",
    )
    source._index.add_event(nested_move, source="zf")
    source._index.commit()

    assert len(source.container_access("Bastard_cj7_green")) == 1
    assert len(source.container_access("Bag2")) == 1
    assert len(source.container_access("Transport_Unrelated")) == 0


def test_container_access_respects_time_window(source):
    event = _item_action(
        _t(100), "Driver", 51,
        source_chain=["Транспортное средство (CarX)"],
        dest_chain=["Персонаж"],
    )
    source._index.add_event(event, source="zf")
    source._index.commit()

    assert len(source.container_access("CarX", since=_t(0), until=_t(10))) == 0
    assert len(source.container_access("CarX", since=_t(90), until=_t(110))) == 1


def test_container_access_matches_container_move_notice_and_details(source):
    event = LogEvent(
        ts=_t(0), kind="container_move", player=None, player_id=None, text="!!!...!!!", raw=None,
        extra={"notice": "предмет был перемещён между двумя контейнерами", "details": "Attack2Bag_Green (ID: 123)"},
    )
    source._index.add_event(event, source="zf")
    source._index.commit()

    assert len(source.container_access("Attack2Bag_Green")) == 1
    assert len(source.container_access("NoSuchItem")) == 0
