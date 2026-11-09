"""Парсер `LBmaster_D_M_YYYY_HH_MM_SS.log` (см. docs/LOGS.md, п.3).

Строка: `HH:MM:SS [LEVEL] [Module] текст`. Дата в файле не повторяется на
каждой строке — берём её из имени файла (`D_M_YYYY` в начале имени), либо из
`base_date`, переданного вызывающим кодом, если имя файла не распознано.

Kinds: `join`, `leave`, `chest_open`, `garage_store`/`garage_restore`
(класс машины + VehicleUID в `extra`) и `admin_action` с подтипами
`teleport`/`spawn`/`delete`/`esp`/`invisible`/`clear_inventory` — подтип
определяется по тексту после `Admin <имя> (<obf_id>) `.

Строка Virtual Garage в доступных образцах docs/LOGS.md не встретилась
буквально — формат ниже подобран по общему виду `[ADMIN] [Module] текст`
этого лога:
    Player <имя> (<steam64>) stored vehicle <класс> (VehicleUID: <uid>)
    Player <имя> (<steам64>) restored vehicle <класс> (VehicleUID: <uid>)

Шумные строки (`Received Watchlist/Banlist ...` и т.п.) не дают события.
Нераспознанные строки пропускаются, счётчики — в `ParseSummary`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

from .base import LogEvent

logger = logging.getLogger(__name__)

_FILENAME_DATE_RE = re.compile(r"LBmaster_(\d{1,2})_(\d{1,2})_(\d{4})_")
_LINE_RE = re.compile(r"^(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2}) \[(?P<level>[A-Z]+)\s*\] \[(?P<module>\w+)\] (?P<text>.*)$")

_JOIN_RE = re.compile(
    r"^Player Joined with Obfuscated ID: (?P<obf>\d+) Real Identity: (?P<name>.+?) \((?P<steam64>\d{17})\)$"
)
_LEAVE_RE = re.compile(r"^Player (?P<name>.+?) \((?P<steam64>\d{17})\) left the server$")
_CHEST_RE = re.compile(r"^Player (?P<name>.+?) \((?P<obf>\d+)\) opened chest at <(?P<pos>[^>]+)>$")
_ADMIN_RE = re.compile(r"^Admin (?P<admin>.+?) \((?P<obf>\d+)\) (?P<action>.+)$")
_GARAGE_RE = re.compile(
    r"^Player (?P<name>.+?) \((?P<steam64>\d{17})\) (?P<verb>stored|restored) vehicle "
    r"(?P<vclass>\S+) \(VehicleUID: (?P<uid>\d+)\)$"
)

_TELEPORT_RE = re.compile(r"^Teleported (?P<target>.+?) to <(?P<pos>[^>]+)>$")
_SPAWN_RE = re.compile(r"^spawned item (?P<item>.+?) at <(?P<pos>[^>]+)>$")
_DELETE_RE = re.compile(r"^deleted item (?P<item>.+?)(?: at <(?P<pos>[^>]+)>)?$")
_ESP_RE = re.compile(r"^toggled ESP: (?P<value>true|false)$")
_INVISIBLE_RE = re.compile(r"^set Modifier INVISIBLE to (?P<value>true|false) for (?P<target>.+)$")
_CLEAR_INVENTORY_RE = re.compile(r"^cleared inventory of (?P<target>.+)$")


@dataclass
class ParseSummary:
    parsed_lines: int = 0
    skipped_lines: int = 0


def _filename_date(path: Path) -> Optional[date]:
    m = _FILENAME_DATE_RE.search(path.name)
    if not m:
        return None
    d, mo, y = (int(v) for v in m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_file(
    path: str | Path, base_date: Optional[date] = None, summary: Optional[ParseSummary] = None
) -> Iterator[LogEvent]:
    path = Path(path)
    the_date = _filename_date(path) or base_date or date.today()

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n\r")
            if not line.strip():
                continue

            line_m = _LINE_RE.match(line)
            if not line_m:
                if summary is not None:
                    summary.skipped_lines += 1
                logger.debug("lbmaster: unparsed line: %r", line)
                continue

            h, mi, s, _level, _module, text = line_m.groups()
            ts = datetime(the_date.year, the_date.month, the_date.day, int(h), int(mi), int(s))

            event = _parse_text(ts, text)
            if event is None:
                if summary is not None:
                    summary.skipped_lines += 1
                continue
            if summary is not None:
                summary.parsed_lines += 1
            yield event


def _parse_text(ts: datetime, text: str) -> Optional[LogEvent]:
    m = _JOIN_RE.match(text)
    if m:
        steam64 = int(m.group("steam64"))
        return LogEvent(
            ts=ts, kind="join", player=m.group("name"), player_id=steam64, text=text, raw=text,
            steam64=steam64, extra={"obfuscated_id": m.group("obf")},
        )

    m = _LEAVE_RE.match(text)
    if m:
        steam64 = int(m.group("steam64"))
        return LogEvent(
            ts=ts, kind="leave", player=m.group("name"), player_id=steam64, text=text, raw=text,
            steam64=steam64,
        )

    m = _CHEST_RE.match(text)
    if m:
        return LogEvent(
            ts=ts, kind="chest_open", player=m.group("name"), player_id=None, text=text, raw=text,
            extra={"obfuscated_id": m.group("obf"), "pos": m.group("pos")},
        )

    m = _GARAGE_RE.match(text)
    if m:
        steam64 = int(m.group("steam64"))
        kind = "garage_store" if m.group("verb") == "stored" else "garage_restore"
        return LogEvent(
            ts=ts, kind=kind, player=m.group("name"), player_id=steam64, text=text, raw=text,
            steam64=steam64, extra={"vehicle_class": m.group("vclass"), "vehicle_uid": m.group("uid")},
        )

    m = _ADMIN_RE.match(text)
    if m:
        return _parse_admin_action(ts, text, m.group("admin"), m.group("obf"), m.group("action"))

    return None


def _parse_admin_action(ts: datetime, text: str, admin: str, obf: str, action: str) -> Optional[LogEvent]:
    base = {
        "ts": ts, "kind": "admin_action", "player": admin, "player_id": None, "text": text, "raw": text,
    }

    m = _TELEPORT_RE.match(action)
    if m:
        return LogEvent(**base, extra={"subtype": "teleport", "obfuscated_id": obf, "target": m.group("target"), "pos": m.group("pos")})

    m = _SPAWN_RE.match(action)
    if m:
        return LogEvent(**base, extra={"subtype": "spawn", "obfuscated_id": obf, "item": m.group("item"), "pos": m.group("pos")})

    m = _DELETE_RE.match(action)
    if m:
        return LogEvent(**base, extra={"subtype": "delete", "obfuscated_id": obf, "item": m.group("item"), "pos": m.group("pos")})

    m = _ESP_RE.match(action)
    if m:
        return LogEvent(**base, extra={"subtype": "esp", "obfuscated_id": obf, "value": m.group("value") == "true"})

    m = _INVISIBLE_RE.match(action)
    if m:
        return LogEvent(**base, extra={"subtype": "invisible", "obfuscated_id": obf, "value": m.group("value") == "true", "target": m.group("target")})

    m = _CLEAR_INVENTORY_RE.match(action)
    if m:
        return LogEvent(**base, extra={"subtype": "clear_inventory", "obfuscated_id": obf, "target": m.group("target")})

    return None
