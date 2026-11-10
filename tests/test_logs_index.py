"""Тесты индексации фикстур tests/fixtures/logs через app.logs.ingest.ingest():
содержимое таблиц events/identities/vehicles и инкрементальность (повторный
обход того же каталога не плодит дублей и не перечитывает уже разобранные файлы).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.logs.index import LogIndex
from app.logs.ingest import ingest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "logs"


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "logs.db")


def test_ingest_parses_all_five_fixture_files(db_path):
    stats = ingest(FIXTURES_DIR, db_path=db_path)

    assert stats["files_parsed"] == 5
    assert stats["files_skipped"] == 0
    assert stats["events"] == 48


def test_ingest_populates_events_table_with_expected_sources_and_kinds(db_path):
    ingest(FIXTURES_DIR, db_path=db_path)

    with LogIndex(db_path) as index:
        rows = index.conn.execute("SELECT source, kind FROM events").fetchall()
        by_source: dict[str, int] = {}
        for row in rows:
            by_source[row["source"]] = by_source.get(row["source"], 0) + 1

        assert len(rows) == 48
        assert by_source == {"adm": 14, "console": 3, "lbmaster": 11, "zf": 18, "carcodelock": 2}

        kinds = {row["kind"] for row in rows}
        assert "roster" in kinds
        assert "vehicle_open" in kinds and "vehicle_close" in kinds
        assert "garage_store" in kinds and "garage_restore" in kinds


def test_ingest_normalizes_coordinates_on_events(db_path):
    ingest(FIXTURES_DIR, db_path=db_path)

    with LogIndex(db_path) as index:
        # CarCodeLock: open на (7609.4, ..., 205.4) -> x=7609.4, z=205.4.
        open_row = index.conn.execute(
            "SELECT x, z FROM events WHERE source = 'carcodelock' AND kind = 'vehicle_open'"
        ).fetchone()
        assert open_row["x"] == 7609.4
        assert open_row["z"] == 205.4

        # ZF roster: Balabaster на <7609.4, 205.4, 13458.6> -> x=7609.4, z=13458.6 (3-й компонент).
        roster_row = index.conn.execute(
            "SELECT x, z FROM events WHERE source = 'zf' AND kind = 'roster' AND player_name = 'Balabaster'"
        ).fetchone()
        assert roster_row["x"] == 7609.4
        assert roster_row["z"] == 13458.6


def test_ingest_populates_identities_table(db_path):
    ingest(FIXTURES_DIR, db_path=db_path)

    with LogIndex(db_path) as index:
        rows = {row["name"]: row for row in index.conn.execute("SELECT * FROM identities")}

        assert set(rows) == {"TerezVds", "Joni", "Kazah", "Nomad", "Balabaster"}

        # ADM: connect даёт bi_hash, нет disconnect для Kazah в фикстуре -> steam64 не известен.
        assert rows["Kazah"]["bi_hash"] == "XWbcLXGVWt0G+XNh2Z0Tor+ONhXjzVy6Gqg/wBGhePc="
        assert rows["Kazah"]["steam64"] is None

        # ADM disconnect: Nomad известен только по steam64 (нет connect-строки для него в фикстуре).
        assert rows["Nomad"]["steam64"] == 76561199000000011

        # console: BE GUID строка и steamID-строка сшиваются в одну запись по имени.
        assert rows["Balabaster"]["steam64"] == 76561199000000021
        assert rows["Balabaster"]["be_guid"] == "0123456789abcdef0123456789abcdef"

        # LBmaster join/leave.
        assert rows["TerezVds"]["steam64"] == 76561199000000031
        assert rows["Joni"]["steam64"] == 76561199000000032


def test_ingest_populates_vehicles_table(db_path):
    ingest(FIXTURES_DIR, db_path=db_path)

    with LogIndex(db_path) as index:
        rows = index.conn.execute(
            "SELECT class, zf_id_or_vehicle_uid, last_pos_x, last_pos_z FROM vehicles ORDER BY class, zf_id_or_vehicle_uid"
        ).fetchall()

        vehicles = {(r["class"], r["zf_id_or_vehicle_uid"]): r for r in rows}
        # LBmaster garage_store/garage_restore -- разные VehicleUID -> разные строки (не UPSERT в одну).
        assert ("CrSk_UAZ_469", "998877") in vehicles
        assert ("CrSk_UAZ_469", "998899") in vehicles
        # ZF vehicle_spawn/vehicle_despawn -- один и тот же vehicle_id -> одна строка с последними координатами.
        assert ("Bastard_cj7_green", "55") in vehicles
        bastard = vehicles[("Bastard_cj7_green", "55")]
        assert bastard["last_pos_x"] == 8090.1
        assert bastard["last_pos_z"] == 9330.9
        assert len(rows) == 3


def test_ingest_is_idempotent_on_repeated_run_same_directory(db_path):
    stats1 = ingest(FIXTURES_DIR, db_path=db_path)
    assert stats1["events"] == 48

    with LogIndex(db_path) as index:
        files_before = {
            row["path"]: (row["parsed_lines"], row["skipped_lines"])
            for row in index.conn.execute("SELECT * FROM files")
        }
        event_count_before = index.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    stats2 = ingest(FIXTURES_DIR, db_path=db_path)

    assert stats2 == {"files_parsed": 0, "files_skipped": 5, "events": 0}

    with LogIndex(db_path) as index:
        files_after = {
            row["path"]: (row["parsed_lines"], row["skipped_lines"])
            for row in index.conn.execute("SELECT * FROM files")
        }
        event_count_after = index.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    assert files_after == files_before
    assert event_count_after == event_count_before == 48


def _make_appendable_copy(tmp_path) -> Path:
    source_dir = tmp_path / "logs_copy"
    source_dir.mkdir()
    carcodelock_src = FIXTURES_DIR / "CarCodeLock_2026-11-09_04-04.log"
    target = source_dir / carcodelock_src.name
    target.write_text(carcodelock_src.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_ingest_reparses_file_and_updates_file_record_after_mtime_changes(db_path, tmp_path):
    """Инкрементальность завязана на size+mtime: если файл реально изменился, его перечитывают
    (а не просто пропускают) -- за это отвечает запись в таблице `files`."""
    target = _make_appendable_copy(tmp_path)
    source_dir = target.parent

    stats1 = ingest(source_dir, db_path=db_path)
    assert stats1 == {"files_parsed": 1, "files_skipped": 0, "events": 2}

    stats2 = ingest(source_dir, db_path=db_path)
    assert stats2 == {"files_parsed": 0, "files_skipped": 1, "events": 0}

    # Трогаем mtime (и добавляем контент), эмулируя дозапись лога на следующей сессии.
    extra_line = (
        "[2026-11-09 05:04:20] [INFO] [CarCodeLock] Player: '76561199000000099', "
        "open transport: 'OtherCar', on position: '1.0 2.0 3.0'\n"
    )
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(extra_line)

    stats3 = ingest(source_dir, db_path=db_path)
    assert stats3 == {"files_parsed": 1, "files_skipped": 0, "events": 3}

    with LogIndex(db_path) as index:
        file_row = index.get_file_status(str(target))
        assert file_row["parsed_lines"] == 3


@pytest.mark.xfail(
    strict=True,
    reason=(
        "НАЙДЕН ДЕФЕКТ (app/logs/ingest.py:_ingest_file): при изменении size/mtime файл "
        "перепарсивается целиком и все его события заново INSERT-ятся в events, без удаления "
        "ранее записанных строк того же файла -- старые события дублируются. После дозаписи "
        "одной новой строки к уже проиндексированному файлу с 2 событиями итоговый events "
        "содержит 5 строк (2 старых + 2 их дубля + 1 новая) вместо ожидаемых 3."
    ),
)
def test_ingest_reparse_after_mtime_change_does_not_duplicate_old_events(db_path, tmp_path):
    target = _make_appendable_copy(tmp_path)
    source_dir = target.parent

    ingest(source_dir, db_path=db_path)

    with open(target, "a", encoding="utf-8") as fh:
        fh.write(
            "[2026-11-09 05:04:20] [INFO] [CarCodeLock] Player: '76561199000000099', "
            "open transport: 'OtherCar', on position: '1.0 2.0 3.0'\n"
        )

    ingest(source_dir, db_path=db_path)

    with LogIndex(db_path) as index:
        total = index.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert total == 3
