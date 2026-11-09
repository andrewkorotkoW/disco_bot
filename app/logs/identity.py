"""Резолвер идентичностей игрока по таблице `identities` (см. docs/LOGS.md,
раздел «Идентичности игрока»): по имени/steam64/BI-hash/BE GUID находит все
известные варианты ключей того же игрока.

Устаревший (переиспользованный разными людьми) ник резолвится в сторону
самой свежей по `last_seen` связки со steam64 — steam64 в ней главный ключ.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .index import LogIndex


@dataclass
class Identity:
    names: set = field(default_factory=set)
    steam64s: set = field(default_factory=set)
    bi_hashes: set = field(default_factory=set)
    be_guids: set = field(default_factory=set)
    steam64: Optional[int] = None


def resolve_identity(key: str, index: LogIndex) -> Identity:
    key = str(key).strip()
    try:
        key_as_steam64: Optional[int] = int(key)
    except ValueError:
        key_as_steam64 = None

    rows = index.find_identity_rows(
        names=(key,) if key_as_steam64 is None else (),
        steam64s=(key_as_steam64,) if key_as_steam64 is not None else (),
        bi_hashes=(key,),
        be_guids=(key,),
    )

    if not rows:
        identity = Identity()
        if key_as_steam64 is not None:
            identity.steam64s.add(key_as_steam64)
            identity.steam64 = key_as_steam64
        else:
            identity.names.add(key)
        return identity

    steam64_groups: dict = {}
    nameless_rows = []
    for row in rows:
        if row["steam64"] is not None:
            steam64_groups.setdefault(row["steam64"], []).append(row)
        else:
            nameless_rows.append(row)

    seed_steam64s: set = set()
    seed_names: set = set()
    seed_bi_hashes: set = set()
    seed_be_guids: set = set()

    if len(steam64_groups) > 1:
        # Ник носили разные игроки — считаем "владельцем" того, кто виден свежее.
        primary = max(steam64_groups, key=lambda s64: max(r["last_seen"] for r in steam64_groups[s64]))
        seed_steam64s.add(primary)
    elif steam64_groups:
        seed_steam64s.update(steam64_groups)
    else:
        for row in nameless_rows:
            if row["name"]:
                seed_names.add(row["name"])
            if row["bi_hash"]:
                seed_bi_hashes.add(row["bi_hash"])
            if row["be_guid"]:
                seed_be_guids.add(row["be_guid"])

    seen_steam64s: set = set()
    seen_names: set = set()
    seen_bi_hashes: set = set()
    seen_be_guids: set = set()
    pending_steam64s = seed_steam64s
    pending_names = seed_names
    pending_bi_hashes = seed_bi_hashes
    pending_be_guids = seed_be_guids
    # Если у нас уже есть "якорный" steam64 (единственный/выбранный как актуальный),
    # общее имя не должно заново подмешивать строки чужого steam64 — иначе
    # устаревший конфликт по нику (см. выше) воскресает на следующем шаге BFS.
    anchored = bool(seed_steam64s)

    while (
        (pending_steam64s - seen_steam64s)
        or (pending_names - seen_names)
        or (pending_bi_hashes - seen_bi_hashes)
        or (pending_be_guids - seen_be_guids)
    ):
        new_steam64s = pending_steam64s - seen_steam64s
        new_names = pending_names - seen_names
        new_bi_hashes = pending_bi_hashes - seen_bi_hashes
        new_be_guids = pending_be_guids - seen_be_guids
        seen_steam64s |= new_steam64s
        seen_names |= new_names
        seen_bi_hashes |= new_bi_hashes
        seen_be_guids |= new_be_guids

        for row in index.find_identity_rows(
            names=tuple(new_names),
            steam64s=tuple(new_steam64s),
            bi_hashes=tuple(new_bi_hashes),
            be_guids=tuple(new_be_guids),
        ):
            row_steam64 = row["steam64"]
            if anchored and row_steam64 is not None and row_steam64 not in pending_steam64s:
                continue
            if row["name"]:
                pending_names.add(row["name"])
            if row_steam64 is not None:
                pending_steam64s.add(row_steam64)
            if row["bi_hash"]:
                pending_bi_hashes.add(row["bi_hash"])
            if row["be_guid"]:
                pending_be_guids.add(row["be_guid"])

    identity = Identity(
        names=seen_names, steam64s=seen_steam64s, bi_hashes=seen_bi_hashes, be_guids=seen_be_guids,
    )
    if seen_steam64s:
        identity.steam64 = next(iter(seen_steam64s))
    return identity
