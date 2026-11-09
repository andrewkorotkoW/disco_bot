"""SQLite-индекс логов сервера: events/identities/vehicles/files (см. docs/LOGS.md).

Путь к БД — аргумент конструктора, либо `LOG_DB_PATH` из env, иначе
`data/logs.db` (по аналогии с `app/cases/store.py` и `DB_PATH`).

Нормализация координат в единые top-level `x`/`z` на `events`/`vehicles`:
большинство парсеров (`adm.py`, `zf.py`, `carcodelock.py`) уже кладут
нормализованные `x`/`z` в сам `LogEvent` (см. их докстринги). Исключение —
`lbmaster.py`: позиция для `admin_action`/`chest_open` остаётся сырой строкой
`"x, y(высота), z"` в `extra['pos']` (порядок как в ADM) — `add_event`
парсит её сама для событий с `source="lbmaster"`.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from .base import LogEvent

DEFAULT_DB_PATH = "data/logs.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    player_name TEXT,
    steam64 INTEGER,
    bi_hash TEXT,
    x REAL,
    z REAL,
    text TEXT,
    extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_steam64 ON events(steam64);
CREATE INDEX IF NOT EXISTS idx_events_bi_hash ON events(bi_hash);
CREATE INDEX IF NOT EXISTS idx_events_player_name ON events(player_name);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);

CREATE TABLE IF NOT EXISTS identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    steam64 INTEGER,
    bi_hash TEXT,
    be_guid TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_identities_name ON identities(name);
CREATE INDEX IF NOT EXISTS idx_identities_steam64 ON identities(steam64);
CREATE INDEX IF NOT EXISTS idx_identities_bi_hash ON identities(bi_hash);
CREATE INDEX IF NOT EXISTS idx_identities_be_guid ON identities(be_guid);

CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class TEXT NOT NULL,
    zf_id_or_vehicle_uid TEXT NOT NULL,
    last_pos_x REAL,
    last_pos_z REAL,
    last_ts TEXT,
    UNIQUE (class, zf_id_or_vehicle_uid)
);

CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    parsed_lines INTEGER NOT NULL,
    skipped_lines INTEGER NOT NULL
);
"""


def _parse_lbmaster_pos(pos: str) -> tuple[Optional[float], Optional[float]]:
    """LBmaster: pos=<x, z, y> (как в ADM) — первое и второе значение."""
    parts = [p.strip() for p in pos.split(",")]
    try:
        return float(parts[0]), float(parts[1])
    except (ValueError, IndexError):
        return None, None


class LogIndex:
    """Тонкая обёртка над SQLite-индексом логов."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.environ.get("LOG_DB_PATH", DEFAULT_DB_PATH)
        self.db_path = db_path
        if db_path != ":memory:":
            parent = Path(db_path).parent
            if str(parent) not in ("", "."):
                parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "LogIndex":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def commit(self) -> None:
        self.conn.commit()

    @staticmethod
    def _ts_iso(ts: datetime) -> str:
        return ts.isoformat()

    def add_event(self, event: LogEvent, source: str) -> None:
        x, z = event.x, event.z
        if x is None and z is None and source == "lbmaster":
            pos = (event.extra or {}).get("pos")
            if isinstance(pos, str):
                x, z = _parse_lbmaster_pos(pos)

        self.conn.execute(
            """
            INSERT INTO events (ts, kind, source, player_name, steam64, bi_hash, x, z, text, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._ts_iso(event.ts),
                event.kind,
                source,
                event.player,
                event.steam64,
                event.bi_hash,
                x,
                z,
                event.text,
                json.dumps(event.extra, ensure_ascii=False) if event.extra is not None else None,
            ),
        )

    def upsert_identity(
        self,
        ts: datetime,
        *,
        name: Optional[str] = None,
        steam64: Optional[int] = None,
        bi_hash: Optional[str] = None,
        be_guid: Optional[str] = None,
    ) -> None:
        if name is None and steam64 is None and bi_hash is None and be_guid is None:
            return
        ts_iso = self._ts_iso(ts)

        # Матчим на самую свежую запись с тем же именем (это естественно сшивает
        # connect-строку с bi_hash и следующую за ней disconnect-строку с steam64
        # той же сессии в одну запись). Если у неё уже есть другой steam64/bi_hash —
        # это другой игрок, когда-то носивший тот же ник: не затираем, создаём новую.
        row = None
        if name is not None:
            row = self.conn.execute(
                "SELECT * FROM identities WHERE name = ? ORDER BY last_seen DESC LIMIT 1", (name,)
            ).fetchone()
        elif steam64 is not None:
            row = self.conn.execute(
                "SELECT * FROM identities WHERE steam64 = ? ORDER BY last_seen DESC LIMIT 1", (steam64,)
            ).fetchone()
        elif bi_hash is not None:
            row = self.conn.execute(
                "SELECT * FROM identities WHERE bi_hash = ?", (bi_hash,)
            ).fetchone()

        conflict = row is not None and (
            (steam64 is not None and row["steam64"] is not None and row["steam64"] != steam64)
            or (bi_hash is not None and row["bi_hash"] is not None and row["bi_hash"] != bi_hash)
        )

        if row is not None and not conflict:
            new_steam64 = steam64 if steam64 is not None else row["steam64"]
            new_bi_hash = bi_hash if bi_hash is not None else row["bi_hash"]
            new_be_guid = be_guid if be_guid is not None else row["be_guid"]
            first_seen = min(row["first_seen"], ts_iso)
            last_seen = max(row["last_seen"], ts_iso)
            self.conn.execute(
                """
                UPDATE identities
                SET steam64 = ?, bi_hash = ?, be_guid = ?, first_seen = ?, last_seen = ?
                WHERE id = ?
                """,
                (new_steam64, new_bi_hash, new_be_guid, first_seen, last_seen, row["id"]),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO identities (name, steam64, bi_hash, be_guid, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, steam64, bi_hash, be_guid, ts_iso, ts_iso),
            )

    def find_identity_rows(
        self,
        *,
        names: Sequence[str] = (),
        steam64s: Sequence[int] = (),
        bi_hashes: Sequence[str] = (),
        be_guids: Sequence[str] = (),
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list = []
        if names:
            clauses.append(f"name IN ({','.join('?' * len(names))})")
            params.extend(names)
        if steam64s:
            clauses.append(f"steam64 IN ({','.join('?' * len(steam64s))})")
            params.extend(steam64s)
        if bi_hashes:
            clauses.append(f"bi_hash IN ({','.join('?' * len(bi_hashes))})")
            params.extend(bi_hashes)
        if be_guids:
            clauses.append(f"be_guid IN ({','.join('?' * len(be_guids))})")
            params.extend(be_guids)
        if not clauses:
            return []
        sql = f"SELECT * FROM identities WHERE {' OR '.join(clauses)}"
        return self.conn.execute(sql, params).fetchall()

    def upsert_vehicle(
        self,
        vclass: Optional[str],
        vehicle_id: Optional[object],
        x: Optional[float],
        z: Optional[float],
        ts: datetime,
    ) -> None:
        if vclass is None or vehicle_id is None:
            return
        vehicle_id = str(vehicle_id)
        ts_iso = self._ts_iso(ts)

        row = self.conn.execute(
            "SELECT * FROM vehicles WHERE class = ? AND zf_id_or_vehicle_uid = ?",
            (vclass, vehicle_id),
        ).fetchone()

        if row is not None:
            if row["last_ts"] and row["last_ts"] > ts_iso:
                return
            new_x = x if x is not None else row["last_pos_x"]
            new_z = z if z is not None else row["last_pos_z"]
            self.conn.execute(
                "UPDATE vehicles SET last_pos_x = ?, last_pos_z = ?, last_ts = ? WHERE id = ?",
                (new_x, new_z, ts_iso, row["id"]),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO vehicles (class, zf_id_or_vehicle_uid, last_pos_x, last_pos_z, last_ts)
                VALUES (?, ?, ?, ?, ?)
                """,
                (vclass, vehicle_id, x, z, ts_iso),
            )

    def get_file_status(self, path: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()

    def record_file(self, path: str, size: int, mtime: float, parsed_lines: int, skipped_lines: int) -> None:
        self.conn.execute(
            """
            INSERT INTO files (path, size, mtime, parsed_lines, skipped_lines)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size = excluded.size, mtime = excluded.mtime,
                parsed_lines = excluded.parsed_lines, skipped_lines = excluded.skipped_lines
            """,
            (path, size, mtime, parsed_lines, skipped_lines),
        )
