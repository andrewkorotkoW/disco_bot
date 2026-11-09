"""Парсер `zfbubusik_log-[DD-MM-YYYY] - HH;MM;SS.log` (см. docs/LOGS.md, п.4).

Строка: `[DD-MM-YYYY] - HH;MM;SS <текст>`, разделитель времени `;` или `:`.
Субъект (имя + steam64) определяется по steam64 (regex 17 цифр), а НЕ по
первому слову — ник может быть вида `[NAPA] Shama` (тег с пробелом).
Субъект `Неизвестно Неизвестно` — ИИ/неигрок, steam64 отсутствует.

Координаты (`x, y(высота), z` в файле) сохраняются как есть в
`extra['pos']` — нормализация в единую систему координат с ADM/CarCodeLock
(там другой порядок) не задача этого парсера, это будет делать индексатор.

Kinds:
- `item_action` — переместил/подобрал/взял/положил/повесил/снял/бросил
  предмет; `extra` содержит `verb`, `item`, `qty`, `item_id` и, когда формат
  строки это позволяет распознать, `source_chain`/`dest_chain` (путь
  контейнера вида `Транспортное средство (X) -> Одежда (Y)`, разбитый по
  `' -> '`; источник/приёмник разделены `' в '`) либо необработанный
  остаток в `tail_raw`.
- `vehicle_action` — сел в / вышел из машины.
- `action` — `выполнил действие <...>` (например, "Завести двигатель").
- `status` — получил урон/порез, заразился/выздоровел, потерял сознание,
  был убит.
- `roster` — блок "Список игроков:". Выбранное решение (см. задачу):
  **один `LogEvent` на игрока** в блоке, а не один событие на весь блок.
  У всех событий одного блока одинаковый `ts` (время снимка), `player`,
  `steam64`, `x`/`z` и `extra['pos']`/`extra['playtime']`.
- `container_move` — строка `!!!предмет был перемещён между двумя
  контейнерами...!!! <детали>`.
- `vehicle_spawn`/`vehicle_despawn` — `инициализировано` /
  `исчезло из игрового мира`, класс машины + координаты в `extra`.

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

_LINE_RE = re.compile(r"^\[(?P<date>\d{2}-\d{2}-\d{4})\] - (?P<h>\d{2})[;:](?P<m>\d{2})[;:](?P<s>\d{2}) (?P<rest>.*)$")

_ROSTER_HEADER_RE = re.compile(r"^- Список игроков:$")
_ROSTER_PLAYER_RE = re.compile(
    r"^Игрок: (?P<name>.+?) \(ID: (?P<steam64>\d{17})\), Местоположение: <(?P<pos>[^>]+)> - , "
    r"Время в игре: (?P<playtime>.+)$"
)
_CONTAINER_MOVE_RE = re.compile(r"^!!!(?P<notice>[^!]+)!!!\s*(?P<details>.*)$")

_UNKNOWN_SUBJECT = "Неизвестно Неизвестно"
_STEAM64_SUBJECT_RE = re.compile(r"^(?P<name>.+?)\s+(?P<steam64>\d{17})\s+(?P<verb_rest>.+)$")
_UNKNOWN_SUBJECT_RE = re.compile(rf"^{re.escape(_UNKNOWN_SUBJECT)}\s+(?P<verb_rest>.+)$")

_VEHICLE_LIFECYCLE_RE = re.compile(
    r"^(?P<vclass>\S+) \(ID: (?P<vid>[^)]+)\) "
    r"(?P<verb>инициализировано|исчезло из игрового мира)(?: в <(?P<pos>[^>]+)>)?$"
)

_ITEM_VERBS = (
    "переместил", "подобрал с земли", "взял с расстояния", "положил",
    "повесил на слот", "снял со слота", "бросил на землю",
)
_STATUS_VERBS = (
    "получил урон от", "получил порез", "заразился болезнью",
    "выздоровел от болезни", "потерял сознание", "был убит",
)

_ITEM_RE = re.compile(r"^(?P<item>.+?) \((?P<qty>\d+)\) \(ID: (?P<item_id>[^)]+)\)(?P<tail>.*)$")
_DISTANCE_PREFIX_RE = re.compile(r"^(?P<distance>[\d.]+)\s*м\s+")
_SEL_V_RE = re.compile(r"^сел в (?P<vclass>.+?)\s*,\s*(?P<coords>.+)$")
_VYSHEL_RE = re.compile(r"^вышел из (?P<vclass>.+)$")
_ACTION_RE = re.compile(r"^выполнил действие <(?P<action>[^>]+)>$")


@dataclass
class ParseSummary:
    parsed_lines: int = 0
    skipped_lines: int = 0


def _parse_pos3(pos: str) -> tuple:
    parts = [p.strip() for p in pos.split(",")]
    try:
        return tuple(float(p) for p in parts)
    except ValueError:
        return (None, None, None)


def parse_file(path: str | Path, summary: Optional[ParseSummary] = None) -> Iterator[LogEvent]:
    roster_ts: Optional[datetime] = None

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n\r")
            if not line.strip():
                continue

            line_m = _LINE_RE.match(line)
            if line_m:
                d, mo, y = (int(v) for v in line_m.group("date").split("-"))
                h, mi, s = int(line_m.group("h")), int(line_m.group("m")), int(line_m.group("s"))
                ts = datetime(y, mo, d, h, mi, s)
                rest = line_m.group("rest")

                if _ROSTER_HEADER_RE.match(rest):
                    roster_ts = ts
                    if summary is not None:
                        summary.skipped_lines += 1
                    continue

                roster_ts = None
                event = _parse_rest(ts, rest, line)
            elif roster_ts is not None:
                m = _ROSTER_PLAYER_RE.match(line)
                if m:
                    event = _build_roster_event(roster_ts, m, line)
                else:
                    roster_ts = None
                    event = None
                    if summary is not None:
                        summary.skipped_lines += 1
                    logger.debug("zf: unparsed line: %r", line)
                    continue
            else:
                if summary is not None:
                    summary.skipped_lines += 1
                logger.debug("zf: unparsed line: %r", line)
                continue

            if event is None:
                if summary is not None:
                    summary.skipped_lines += 1
                continue
            if summary is not None:
                summary.parsed_lines += 1
            yield event


def _build_roster_event(ts: datetime, m: re.Match, raw: str) -> LogEvent:
    steam64 = int(m.group("steam64"))
    pos = _parse_pos3(m.group("pos"))
    return LogEvent(
        ts=ts, kind="roster", player=m.group("name"), player_id=steam64, text=raw, raw=raw,
        steam64=steam64, x=pos[0], z=pos[2],
        extra={"pos": pos, "playtime": m.group("playtime")},
    )


def _parse_rest(ts: datetime, rest: str, raw: str) -> Optional[LogEvent]:
    m = _CONTAINER_MOVE_RE.match(rest)
    if m:
        return LogEvent(
            ts=ts, kind="container_move", player=None, player_id=None, text=rest, raw=raw,
            extra={"notice": m.group("notice").strip(), "details": m.group("details").strip()},
        )

    m = _VEHICLE_LIFECYCLE_RE.match(rest)
    if m:
        kind = "vehicle_spawn" if m.group("verb") == "инициализировано" else "vehicle_despawn"
        pos = _parse_pos3(m.group("pos")) if m.group("pos") else (None, None, None)
        return LogEvent(
            ts=ts, kind=kind, player=None, player_id=None, text=rest, raw=raw,
            x=pos[0], z=pos[2],
            extra={"vehicle_class": m.group("vclass"), "vehicle_id": m.group("vid"), "pos": pos},
        )

    m = _STEAM64_SUBJECT_RE.match(rest)
    if m:
        name = m.group("name")
        steam64 = int(m.group("steam64"))
        verb_rest = m.group("verb_rest")
        return _parse_subject_action(ts, raw, name, steam64, verb_rest)

    m = _UNKNOWN_SUBJECT_RE.match(rest)
    if m:
        return _parse_subject_action(ts, raw, _UNKNOWN_SUBJECT, None, m.group("verb_rest"))

    return None


def _parse_subject_action(
    ts: datetime, raw: str, name: str, steam64: Optional[int], verb_rest: str
) -> Optional[LogEvent]:
    for verb in _ITEM_VERBS:
        if verb_rest.startswith(verb + " "):
            item_rest = verb_rest[len(verb) + 1:]
            distance = None
            dm = _DISTANCE_PREFIX_RE.match(item_rest)
            if dm:
                distance = float(dm.group("distance"))
                item_rest = item_rest[dm.end():]
            return _build_item_event(ts, raw, name, steam64, verb, item_rest, distance)

    m = _SEL_V_RE.match(verb_rest)
    if m:
        return LogEvent(
            ts=ts, kind="vehicle_action", player=name, player_id=steam64, text=verb_rest, raw=raw,
            steam64=steam64, extra={"verb": "сел в", "vehicle_class": m.group("vclass"), "coords": m.group("coords")},
        )

    m = _VYSHEL_RE.match(verb_rest)
    if m:
        return LogEvent(
            ts=ts, kind="vehicle_action", player=name, player_id=steam64, text=verb_rest, raw=raw,
            steam64=steam64, extra={"verb": "вышел из", "vehicle_class": m.group("vclass")},
        )

    m = _ACTION_RE.match(verb_rest)
    if m:
        return LogEvent(
            ts=ts, kind="action", player=name, player_id=steam64, text=verb_rest, raw=raw,
            steam64=steam64, extra={"verb": "выполнил действие", "action": m.group("action")},
        )

    for verb in _STATUS_VERBS:
        if verb_rest.startswith(verb):
            detail = verb_rest[len(verb):].strip()
            return LogEvent(
                ts=ts, kind="status", player=name, player_id=steam64, text=verb_rest, raw=raw,
                steam64=steam64, extra={"verb": verb, "detail": detail or None},
            )

    return None


def _build_item_event(
    ts: datetime, raw: str, name: str, steam64: Optional[int], verb: str, item_rest: str,
    distance: Optional[float] = None,
) -> Optional[LogEvent]:
    m = _ITEM_RE.match(item_rest)
    if not m:
        return LogEvent(
            ts=ts, kind="item_action", player=name, player_id=steam64, text=item_rest, raw=raw,
            steam64=steam64, extra={"verb": verb, "tail_raw": item_rest, "distance": distance},
        )

    extra = {"verb": verb, "item": m.group("item"), "qty": int(m.group("qty")), "item_id": m.group("item_id")}
    if distance is not None:
        extra["distance"] = distance
    tail = m.group("tail").strip()
    if tail.startswith("из "):
        tail = tail[len("из "):]
        if " в " in tail:
            src, dest = tail.split(" в ", 1)
            extra["source_chain"] = [s.strip() for s in src.strip().split(" -> ")]
            extra["dest_chain"] = [s.strip() for s in dest.strip().split(" -> ")]
        else:
            extra["source_chain"] = [s.strip() for s in tail.strip().split(" -> ")]
    elif tail:
        extra["tail_raw"] = tail

    return LogEvent(
        ts=ts, kind="item_action", player=name, player_id=steam64, text=item_rest, raw=raw,
        steam64=steam64, extra=extra,
    )
