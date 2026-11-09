"""Парсер `CarCodeLock_YYYY-MM-DD_HH-MM.log` (см. docs/LOGS.md, п.5).

Строка:
    [YYYY-MM-DD HH:MM:SS] [LEVEL] [CarCodeLock] Player: 'steam64', open transport: 'класс', on position: 'x y z'
    [...] close transport: ...

Kinds: `vehicle_open`, `vehicle_close`. Позиция `'x y z'` — три числа через
пробел в порядке x, y (высота), z; `x`/`z` кладём в одноимённые поля
`LogEvent`, все три числа как есть — в `extra['pos']`.

Нераспознанные строки пропускаются, счётчики — в `ParseSummary`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .base import LogEvent

logger = logging.getLogger(__name__)

_LINE_RE = re.compile(
    r"^\[(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2}:\d{2})\] \[(?P<level>\w+)\] \[CarCodeLock\] "
    r"Player: '(?P<steam64>\d{17})', (?P<action>open|close) transport: '(?P<vclass>[^']+)', "
    r"on position: '(?P<pos>[^']+)'$"
)


@dataclass
class ParseSummary:
    parsed_lines: int = 0
    skipped_lines: int = 0


def parse_file(path: str | Path, summary: Optional[ParseSummary] = None) -> Iterator[LogEvent]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n\r")
            if not line.strip():
                continue

            m = _LINE_RE.match(line)
            if not m:
                if summary is not None:
                    summary.skipped_lines += 1
                logger.debug("carcodelock: unparsed line: %r", line)
                continue

            event = _build_event(m, line)
            if event is None:
                if summary is not None:
                    summary.skipped_lines += 1
                continue
            if summary is not None:
                summary.parsed_lines += 1
            yield event


def _build_event(m: re.Match, raw: str) -> Optional[LogEvent]:
    y, mo, d = (int(v) for v in m.group("date").split("-"))
    h, mi, s = (int(v) for v in m.group("time").split(":"))
    ts = datetime(y, mo, d, h, mi, s)

    steam64 = int(m.group("steam64"))
    kind = "vehicle_open" if m.group("action") == "open" else "vehicle_close"

    coords = m.group("pos").split()
    try:
        x, y_, z = (float(v) for v in coords)
    except ValueError:
        x = y_ = z = None

    return LogEvent(
        ts=ts, kind=kind, player=None, player_id=steam64,
        text=f"{m.group('action')} transport {m.group('vclass')}", raw=raw,
        steam64=steam64, x=x, z=z,
        extra={"vehicle_class": m.group("vclass"), "pos": (x, y_, z)},
    )
