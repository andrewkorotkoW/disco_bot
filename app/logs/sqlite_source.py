"""`SqliteLogSource` — реализация протокола `LogSource` (см. `base.py`) над
индексом логов (`index.py`), плюс запросы под конкретные типы тикетов из
docs/LOGS.md («Что бот должен уметь доставать по тикету»).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from typing import Optional, Sequence

from .base import LogEvent
from .identity import Identity, resolve_identity
from .index import LogIndex

_VEHICLE_KINDS = (
    "vehicle_open", "vehicle_close", "vehicle_action",
    "vehicle_spawn", "vehicle_despawn", "garage_store", "garage_restore",
)
_OFFLINE_KINDS = ("connect", "disconnect", "identity")


def _identity_where(identity: Identity) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    if identity.names:
        clauses.append(f"player_name IN ({','.join('?' * len(identity.names))})")
        params.extend(identity.names)
    if identity.steam64s:
        clauses.append(f"steam64 IN ({','.join('?' * len(identity.steam64s))})")
        params.extend(identity.steam64s)
    if identity.bi_hashes:
        clauses.append(f"bi_hash IN ({','.join('?' * len(identity.bi_hashes))})")
        params.extend(identity.bi_hashes)
    if not clauses:
        return "", []
    return "(" + " OR ".join(clauses) + ")", params


class SqliteLogSource:
    def __init__(self, db_path: Optional[str] = None):
        self._index = LogIndex(db_path)

    def close(self) -> None:
        self._index.close()

    def __enter__(self) -> "SqliteLogSource":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def resolve(self, key: str) -> Identity:
        return resolve_identity(key, self._index)

    @staticmethod
    def _row_to_event(row) -> LogEvent:
        extra = json.loads(row["extra"]) if row["extra"] else None
        return LogEvent(
            ts=datetime.fromisoformat(row["ts"]),
            kind=row["kind"],
            player=row["player_name"],
            player_id=row["steam64"],
            text=row["text"],
            raw=dict(row),
            bi_hash=row["bi_hash"],
            steam64=row["steam64"],
            x=row["x"],
            z=row["z"],
            extra=extra,
        )

    def query(
        self,
        player: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        kinds: Optional[Sequence[str]] = None,
    ) -> list[LogEvent]:
        clauses: list[str] = []
        params: list = []

        if player is not None:
            identity = self.resolve(player)
            id_clause, id_params = _identity_where(identity)
            if not id_clause:
                return []
            clauses.append(id_clause)
            params.extend(id_params)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("ts <= ?")
            params.append(until.isoformat())
        if kinds:
            clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)

        sql = "SELECT * FROM events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts"
        rows = self._index.conn.execute(sql, params).fetchall()
        return [self._row_to_event(row) for row in rows]

    def timeline(
        self, player: str, since: Optional[datetime] = None, until: Optional[datetime] = None
    ) -> list[LogEvent]:
        return self.query(player=player, since=since, until=until)

    def nearby_players(
        self, x: float, z: float, radius: float, at: datetime, window_minutes: int = 10
    ) -> list[dict]:
        window = timedelta(minutes=window_minutes)
        rows = self._index.conn.execute(
            """
            SELECT * FROM events
            WHERE source = 'zf' AND kind = 'roster' AND x IS NOT NULL AND z IS NOT NULL
              AND ts BETWEEN ? AND ?
            """,
            ((at - window).isoformat(), (at + window).isoformat()),
        ).fetchall()

        results = []
        for row in rows:
            distance = math.hypot(row["x"] - x, row["z"] - z)
            if distance <= radius:
                results.append(
                    {"player": row["player_name"], "steam64": row["steam64"], "distance": distance, "ts": row["ts"]}
                )
        results.sort(key=lambda r: r["distance"])
        return results

    def vehicle_history(
        self, vehicle_class: str, since: Optional[datetime] = None, until: Optional[datetime] = None
    ) -> list[LogEvent]:
        clauses = [f"kind IN ({','.join('?' * len(_VEHICLE_KINDS))})"]
        params: list = list(_VEHICLE_KINDS)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("ts <= ?")
            params.append(until.isoformat())
        sql = "SELECT * FROM events WHERE " + " AND ".join(clauses) + " ORDER BY ts"
        rows = self._index.conn.execute(sql, params).fetchall()
        events = [self._row_to_event(row) for row in rows]
        return [e for e in events if (e.extra or {}).get("vehicle_class") == vehicle_class]

    def offline_windows(self, player: str) -> list[tuple[datetime, Optional[datetime]]]:
        events = self.query(player=player, kinds=_OFFLINE_KINDS)
        windows: list[tuple[datetime, Optional[datetime]]] = []
        pending_disconnect: Optional[datetime] = None
        for event in events:
            if event.kind == "disconnect":
                pending_disconnect = event.ts
            elif pending_disconnect is not None:
                windows.append((pending_disconnect, event.ts))
                pending_disconnect = None
        if pending_disconnect is not None:
            windows.append((pending_disconnect, None))
        return windows

    def container_access(
        self, container_class_or_vehicle: str, since: Optional[datetime] = None, until: Optional[datetime] = None
    ) -> list[LogEvent]:
        """Кто брал/класл из контейнера или машины `container_class_or_vehicle` —
        событие попадает в результат, если этот класс является КОРНЕМ src или dst
        цепочки (`extra['src_root_class']`/`extra['dst_root_class']`, см. zf.py) —
        не просто встречается где-то в цепочке. Для событий без распознанных
        цепочек (например, `container_move` с нераспознанным `details`) — запасной
        вариант: подстрока в `notice`/`details`."""
        clauses = ["source = 'zf'", "kind IN ('item_action', 'container_move')"]
        params: list = []
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("ts <= ?")
            params.append(until.isoformat())
        sql = "SELECT * FROM events WHERE " + " AND ".join(clauses) + " ORDER BY ts"
        rows = self._index.conn.execute(sql, params).fetchall()
        events = [self._row_to_event(row) for row in rows]

        matched = []
        for event in events:
            extra = event.extra or {}
            src_root = extra.get("src_root_class")
            dst_root = extra.get("dst_root_class")
            if src_root is not None or dst_root is not None:
                if src_root == container_class_or_vehicle or dst_root == container_class_or_vehicle:
                    matched.append(event)
                continue

            # Нет root-полей (старые данные/нераспознанная цепочка) -- запасной
            # поиск подстроки по всей цепочке и по сырому notice/details.
            chain = (
                list(extra.get("source_chain") or extra.get("src_chain") or [])
                + list(extra.get("dest_chain") or extra.get("dst_chain") or [])
            )
            if any(container_class_or_vehicle in part for part in chain):
                matched.append(event)
            elif event.kind == "container_move":
                haystack = f"{extra.get('notice', '')} {extra.get('details', '')}"
                if container_class_or_vehicle in haystack:
                    matched.append(event)
        return matched

    def nearby_during_offline(
        self,
        player: str,
        pos: tuple[float, float],
        radius: float,
        *,
        window_minutes: int = 10,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> list[dict]:
        """Для каждого окна офлайна `player` (`offline_windows`), пересекающегося с
        `[since, until]`, ищет (`nearby_players`) всех в радиусе `radius` от `pos`
        вокруг НАЧАЛА и, если окно закрыто, КОНЦА этого окна — «кто был в N м от
        последней позиции машины/игрока, пока владелец был офлайн» (docs/LOGS.md,
        п.5б). `pos` — (x, z), её источник (последняя позиция машины или самого
        игрока) определяет вызывающий код (`app/logs/facts.py`), т.к. зависит от
        типа тикета."""
        x, z = pos
        results: list[dict] = []
        for start, end in self.offline_windows(player):
            if until is not None and start > until:
                continue
            if since is not None and end is not None and end < since:
                continue
            for at in (start,) if end is None else (start, end):
                for row in self.nearby_players(x, z, radius, at, window_minutes=window_minutes):
                    enriched = dict(row)
                    enriched["window_start"] = start
                    enriched["window_end"] = end
                    enriched["checked_at"] = at
                    results.append(enriched)
        return results
