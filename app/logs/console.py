"""Парсер `server_console.log` (см. docs/LOGS.md, п.2).

Парсим ТОЛЬКО строки BattlEye — единственное место, где связаны имя, steam64
и BI-hash (uid) в момент коннекта:

- `BattlEye Server: Player #N name (ip) connected` -> kind=be_connect
- `BattlEye Server: Player #N name - BE GUID: <hex32>` -> kind=identity (be_guid)
- `Player "name" (steamID=... pos=<...>) is connected` -> kind=identity (steam64)

Всё остальное (FPS, конфиг BattlEye, `RConPassword`, спавн лута и т.п.) —
намеренно игнорируется. IP-адрес из первой строки НЕ попадает ни в одно поле
`LogEvent` (ни в text/raw/extra) — только номер слота и имя.

Строка лога не содержит даты (только `H:MM:SS` без ведущего нуля) — дата
сессии неизвестна парсеру и должна быть передана вызывающим кодом через
`base_date` (например, определена индексатором по имени папки сессии).
Без `base_date` используется текущая дата (`date.today()`).

`parse_file(path, base_date=None, summary=None)` — генератор `LogEvent`,
нераспознанные строки пропускаются и считаются в `summary.skipped_lines`
(см. `ParseSummary`).
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

_LINE_RE = re.compile(r"^\s*(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2}) (?P<rest>.*)$")

_BE_CONNECT_RE = re.compile(
    r"^BattlEye Server: Player #(?P<slot>\d+) (?P<name>.+?) \([^)]*\) connected$"
)
_BE_GUID_RE = re.compile(
    r"^BattlEye Server: Player #(?P<slot>\d+) (?P<name>.+?) - BE GUID: (?P<guid>[0-9a-fA-F]{32})$"
)
_STEAM_CONNECT_RE = re.compile(
    r'^Player "(?P<name>[^"]+)" \(steamID=(?P<steam64>\d{17}) pos=<(?P<pos>[^>]+)>\) is connected$'
)


@dataclass
class ParseSummary:
    parsed_lines: int = 0
    skipped_lines: int = 0


def parse_file(
    path: str | Path, base_date: Optional[date] = None, summary: Optional[ParseSummary] = None
) -> Iterator[LogEvent]:
    the_date = base_date or date.today()

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n\r")
            if not line.strip():
                continue

            line_m = _LINE_RE.match(line)
            if not line_m:
                if summary is not None:
                    summary.skipped_lines += 1
                logger.debug("console: unparsed line: %r", line)
                continue

            h, mi, s, rest = line_m.groups()
            ts = datetime(the_date.year, the_date.month, the_date.day, int(h), int(mi), int(s))

            event = _parse_rest(ts, rest)
            if event is None:
                if summary is not None:
                    summary.skipped_lines += 1
                continue
            if summary is not None:
                summary.parsed_lines += 1
            yield event


def _parse_rest(ts: datetime, rest: str) -> Optional[LogEvent]:
    m = _BE_CONNECT_RE.match(rest)
    if m:
        text = f"Player #{m.group('slot')} {m.group('name')} connected"
        return LogEvent(
            ts=ts, kind="be_connect", player=m.group("name"), player_id=None, text=text, raw=text,
            extra={"slot": int(m.group("slot"))},
        )

    m = _BE_GUID_RE.match(rest)
    if m:
        text = f"Player #{m.group('slot')} {m.group('name')} - BE GUID: {m.group('guid')}"
        return LogEvent(
            ts=ts, kind="identity", player=m.group("name"), player_id=None, text=text, raw=text,
            extra={"slot": int(m.group("slot")), "be_guid": m.group("guid")},
        )

    m = _STEAM_CONNECT_RE.match(rest)
    if m:
        steam64 = int(m.group("steam64"))
        text = f'Player "{m.group("name")}" (steamID={steam64}) is connected'
        return LogEvent(
            ts=ts, kind="identity", player=m.group("name"), player_id=steam64, text=text, raw=text,
            steam64=steam64, extra={"pos": m.group("pos")},
        )

    return None
