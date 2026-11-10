"""Тесты резолвера идентичностей (app.logs.identity.resolve_identity) на
таблице identities, наполненной напрямую через LogIndex.upsert_identity --
без парсинга реальных файлов, чтобы точно контролировать порядок событий
(это то, что делает `_update_identity` в ingest.py на реальных connect/disconnect)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.logs.identity import resolve_identity
from app.logs.index import LogIndex

T0 = datetime(2026, 11, 9, 4, 0, 0)


def _t(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


@pytest.fixture
def index():
    idx = LogIndex(":memory:")
    yield idx
    idx.close()


def test_resolve_by_name_steam64_or_bi_hash_returns_same_identity(index):
    """Один игрок: ADM connect даёт bi_hash, следующий disconnect той же сессии -- steam64.
    Резолв по нику, по steam64 (строкой) и по bi_hash должен давать один и тот же набор ключей."""
    index.upsert_identity(_t(0), name="Kazah", bi_hash="HASH_KAZAH")
    index.upsert_identity(_t(5), name="Kazah", steam64=111)
    index.commit()

    by_name = resolve_identity("Kazah", index)
    by_steam64 = resolve_identity("111", index)
    by_bi_hash = resolve_identity("HASH_KAZAH", index)

    for identity in (by_name, by_steam64, by_bi_hash):
        assert identity.names == {"Kazah"}
        assert identity.steam64s == {111}
        assert identity.bi_hashes == {"HASH_KAZAH"}
        assert identity.steam64 == 111


def test_nickname_change_for_same_steam64_resolves_both_directions(index):
    """Игрок со steam64=400 сменил ник: OldNick -> NewNick. Резолв по любому из двух
    ников должен находить оба ника, сшитые по общему steam64."""
    index.upsert_identity(_t(0), name="OldNick", bi_hash="HASH_OLD")
    index.upsert_identity(_t(1), name="OldNick", steam64=400)
    index.upsert_identity(_t(10), name="NewNick", bi_hash="HASH_NEW")
    index.upsert_identity(_t(11), name="NewNick", steam64=400)
    index.commit()

    by_old = resolve_identity("OldNick", index)
    by_new = resolve_identity("NewNick", index)

    for identity in (by_old, by_new):
        assert identity.names == {"OldNick", "NewNick"}
        assert identity.steam64s == {400}
        assert identity.bi_hashes == {"HASH_OLD", "HASH_NEW"}
        assert identity.steam64 == 400


def test_nickname_reuse_by_different_players_resolves_by_recency(index):
    """Нику "Pirate" последовательно владели два разных игрока (разные steam64).
    Резолв по нику должен указывать на самого свежего владельца (по last_seen),
    а резолв по старому steam64 -- на старого, не смешивая их."""
    index.upsert_identity(_t(0), name="Pirate", bi_hash="HASH_A")
    index.upsert_identity(_t(1), name="Pirate", steam64=200)  # старый игрок, steam64=200

    index.upsert_identity(_t(10), name="Pirate", bi_hash="HASH_B")  # новый игрок взял тот же ник
    index.upsert_identity(_t(11), name="Pirate", steam64=300)  # новый игрок, steam64=300
    index.commit()

    by_name = resolve_identity("Pirate", index)
    assert by_name.steam64 == 300
    assert by_name.steam64s == {300}
    assert by_name.bi_hashes == {"HASH_B"}

    by_old_steam64 = resolve_identity("200", index)
    assert by_old_steam64.steam64 == 200
    assert by_old_steam64.steam64s == {200}
    assert by_old_steam64.bi_hashes == {"HASH_A"}
    assert "Pirate" in by_old_steam64.names


def test_resolve_unknown_name_returns_identity_seeded_with_that_name(index):
    identity = resolve_identity("NeverSeenPlayer", index)

    assert identity.names == {"NeverSeenPlayer"}
    assert identity.steam64s == set()
    assert identity.steam64 is None


def test_resolve_unknown_steam64_returns_identity_seeded_with_that_steam64(index):
    identity = resolve_identity("76561199999999999", index)

    assert identity.steam64s == {76561199999999999}
    assert identity.steam64 == 76561199999999999
    assert identity.names == set()
