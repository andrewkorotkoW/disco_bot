"""Тесты app/logs: интерфейс LogSource и заглушка NullLogSource."""
from __future__ import annotations

from datetime import datetime, timezone

from app.logs import LogEvent, NullLogSource


def test_null_log_source_returns_empty_list_without_args():
    source = NullLogSource()
    assert source.query() == []


def test_null_log_source_returns_empty_list_with_any_args():
    source = NullLogSource()

    result = source.query(
        player="Vasya",
        since=datetime(2024, 1, 1, tzinfo=timezone.utc),
        until=datetime(2024, 1, 2, tzinfo=timezone.utc),
        kinds=["kill", "chat"],
    )

    assert result == []


def test_log_event_fields():
    ts = datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc)
    event = LogEvent(ts=ts, kind="kill", player="Vasya", player_id=123, text="killed Petya", raw={"x": 1})

    assert event.ts == ts
    assert event.kind == "kill"
    assert event.player == "Vasya"
    assert event.player_id == 123
    assert event.text == "killed Petya"
    assert event.raw == {"x": 1}


def test_log_event_optional_player_fields_can_be_none():
    event = LogEvent(
        ts=datetime.now(timezone.utc), kind="system", player=None, player_id=None, text="restart", raw=None
    )

    assert event.player is None
    assert event.player_id is None
