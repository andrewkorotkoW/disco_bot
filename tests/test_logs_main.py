"""Тесты CLI app/logs/__main__.py: команда `container` (остальные команды покрыты
e2e через ingest+sqlite_source в test_logs_index.py/test_logs_sqlite_source.py)."""
from __future__ import annotations

from datetime import datetime

from app.logs.__main__ import main
from app.logs.base import LogEvent
from app.logs.index import LogIndex


def test_cmd_container_prints_events_and_actors_summary(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "logs.db")
    monkeypatch.setenv("LOG_DB_PATH", db_path)

    with LogIndex(db_path) as index:
        index.add_event(
            LogEvent(
                ts=datetime(2026, 11, 9, 4, 5, 0),
                kind="item_action", player="Driver", player_id=51, text="переместил SomeItem", raw=None,
                extra={
                    "verb": "переместил", "item": "SomeItem",
                    "src_root_class": "Bastard_cj7_green", "dst_root_class": None,
                },
            ),
            source="zf",
        )
        index.commit()

    rc = main(["container", "Bastard_cj7_green"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Driver" in out
    assert "всего событий: 1" in out
    assert "брали/класли: Driver" in out


def test_cmd_container_no_match_reports_zero_events(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "logs.db")
    monkeypatch.setenv("LOG_DB_PATH", db_path)
    with LogIndex(db_path):
        pass

    rc = main(["container", "NoSuchClass"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "всего событий: 0" in out
    assert "брали/класли: —" in out
