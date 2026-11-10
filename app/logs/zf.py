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
  предмет; `extra` содержит `verb`, `item`/`item_class` (одно и то же),
  `qty`, `item_id`, `distance` (когда есть в строке — префиксом у «взял с
  расстояния» или суффиксом `с расстояния N м.` у остальных глаголов) и,
  когда формат строки это позволяет распознать, цепочки контейнеров
  `src_chain`/`source_chain` и `dst_chain`/`dest_chain` (путь вида
  `Персонаж (X) -> Одежда (Y)`, разбитый по `' -> '`; если в строке есть
  обе стороны — они разделены `' в '`, иначе единственная цепочка относится
  к источнику или приёмнику в зависимости от глагола), `src_root_class`/
  `dst_root_class` (класс из первого звена соответствующей цепочки) и
  `pos` (координаты в конце строки, когда есть) либо необработанный
  остаток в `tail_raw`.
- `vehicle_action` — сел в / вышел из машины; `extra['vehicle_class']` —
  только класс, `extra['zf_id']` — ID машины и `extra['coords']` —
  координаты, когда они есть в строке.
- `action` — `выполнил действие <...>` (например, "Завести двигатель").
- `status` — получил урон/порез, заразился/выздоровел, потерял сознание.
- `death` — был убит; `extra` содержит `killer_kind` (`bot`/`player`/
  `env`), `killer_name`, `killer_steam64` (только для `player`), `weapon`,
  `cause` (сырой текст «Причина смерти»), `distance`, `pos` (позиция
  жертвы).
- `roster` — блок "Список игроков:". Выбранное решение (см. задачу):
  **один `LogEvent` на игрока** в блоке, а не один событие на весь блок.
  У всех событий одного блока одинаковый `ts` (время снимка), `player`,
  `steam64`, `x`/`z` и `extra['pos']`/`extra['playtime']`.
- `container_move` — строка `!!!предмет был перемещён между двумя
  контейнерами...!!! <детали>`. `extra['details']`/`extra['notice']` —
  сырой текст (как раньше), плюс те же поля, что и у `item_action`
  (`verb`, `item_class`, `qty`, `item_id`, `distance`, `src_chain`/
  `dst_chain`, `src_root_class`/`dst_root_class`, `pos`), когда `details`
  распознаётся, плюс `extra['actor_class']` — класс актора (бота/NPC) из
  той же цепочки.
- `vehicle_spawn`/`vehicle_despawn` — `инициализировано` /
  `исчезло из игрового мира`, `extra['vehicle_class']` — только класс,
  `extra['vehicle_id']` — ID, `extra['pos']` — координаты.

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

# Живой формат: `Транспортное средство <класс> (ID: <id>) инициализировано по
# координатам x,y,z`; старый (синтетический) формат без слова «Транспортное
# средство» и с координатами в `<...>` после «в» — оба распознаются.
_VEHICLE_LIFECYCLE_RE = re.compile(
    r"^(?:Транспортное средство\s+)?(?P<vclass>\S+) \(ID: (?P<vid>[^)]+)\) "
    r"(?P<verb>инициализировано|исчезло из игрового мира)"
    r"(?:\s+(?:в|по координатам)\s+<?(?P<pos>[^>]+?)>?)?\s*$"
)

_ITEM_VERBS = (
    "переместил", "подобрал с земли", "взял с расстояния", "положил",
    "повесил на слот", "снял со слота", "бросил на землю",
)
_STATUS_VERBS = (
    "получил урон от", "получил порез", "заразился болезнью",
    "выздоровел от болезни", "потерял сознание",
)

_ITEM_RE = re.compile(r"^(?P<item>.+?)(?: \((?P<qty>[\d.]+)\))? \(ID: (?P<item_id>[^)]+)\)(?P<tail>.*)$")
_DISTANCE_PREFIX_RE = re.compile(r"^(?P<distance>[\d.]+)\s*м\s+")
_DISTANCE_SUFFIX_RE = re.compile(r"^с расстояния (?P<distance>[\d.]+)\s*м\.?\s*")
_CHAIN_ROOT_CLASS_RE = re.compile(r"\(([^()]+)\)\s*$")
_SRC_CHAIN_VERBS = {"снял со слота", "бросил на землю"}
_DST_CHAIN_VERBS = {"подобрал с земли", "взял с расстояния", "положил", "повесил на слот"}

# `сел в <класс> (ID: <id>), x,y,z` / `вышел из <класс> (ID: <id>), x,y,z` —
# ID и координаты в реальных логах есть у обоих глаголов; старый
# (синтетический) формат без ID и/или без координат тоже распознаётся.
_SEL_V_RE = re.compile(r"^сел в (?P<rest>.+)$")
_VYSHEL_RE = re.compile(r"^вышел из (?P<rest>.+)$")
_VEHICLE_SUFFIX_RE = re.compile(r"^(?P<vclass>\S+)(?: \(ID: (?P<vid>[^)]+)\))?\s*(?:,\s*(?P<coords>.+))?$")

_ACTION_RE = re.compile(r"^выполнил действие <(?P<action>[^>]+)>$")

_DEATH_RE = re.compile(
    r"^\. (?P<killer_info>.*?), Расстояние: (?P<distance>[\d.]+) м\.?,\s*"
    r"Причина смерти: (?P<cause>.*?), Позиция жертвы: (?P<pos>[^,]+,[^,]+,[^,]+).*$"
)
_KILLER_BOT_RE = re.compile(r"^Бот EAi: (?P<name>.+?) с (?P<weapon>.+)$")
_KILLER_PLAYER_RE = re.compile(r"^Игрок: (?P<name>.+?) \((?P<steam64>\d{17})\) с (?P<weapon>.+)$")


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


def _parse_qty(raw: Optional[str]):
    if raw is None:
        return 1
    try:
        return int(raw)
    except ValueError:
        return float(raw)


def _split_chain(chain_str: str) -> list:
    return [s.strip() for s in chain_str.strip().split(" -> ") if s.strip()]


def _root_class(chain: list) -> Optional[str]:
    if not chain:
        return None
    m = _CHAIN_ROOT_CLASS_RE.search(chain[0])
    return m.group(1) if m else None


def _parse_item_tail(tail: str, verb: str, extra: dict) -> None:
    """Разбирает остаток строки после `(ID: ...)`: расстояние (если не
    извлечено раньше), цепочки контейнеров и их корневые классы."""
    tail = tail.strip()
    if not tail:
        return

    dm = _DISTANCE_SUFFIX_RE.match(tail)
    if dm and "distance" not in extra:
        extra["distance"] = float(dm.group("distance"))
        tail = tail[dm.end():].strip()

    if tail.startswith("из "):
        tail = tail[len("из "):]
    if not tail:
        return

    pos_part = None
    if ":" in tail:
        chain_part, _, maybe_pos = tail.rpartition(":")
        maybe_pos = maybe_pos.strip()
        if maybe_pos and all(c.isdigit() or c in ".,-" or c.isspace() for c in maybe_pos):
            tail = chain_part.strip()
            pos_part = maybe_pos
    if pos_part:
        extra["pos"] = _parse_pos3(pos_part)
    if not tail:
        return

    if " в " in tail:
        src_part, dst_part = tail.split(" в ", 1)
        extra["src_chain"] = extra["source_chain"] = _split_chain(src_part)
        extra["dst_chain"] = extra["dest_chain"] = _split_chain(dst_part)
    else:
        chain = _split_chain(tail)
        if verb in _SRC_CHAIN_VERBS:
            extra["src_chain"] = extra["source_chain"] = chain
        else:
            extra["dst_chain"] = extra["dest_chain"] = chain

    if extra.get("src_chain"):
        extra["src_root_class"] = _root_class(extra["src_chain"])
    if extra.get("dst_chain"):
        extra["dst_root_class"] = _root_class(extra["dst_chain"])


def _parse_vehicle_suffix(rest: str) -> tuple:
    m = _VEHICLE_SUFFIX_RE.match(rest.strip())
    if not m:
        return rest.strip(), None, None
    return m.group("vclass"), m.group("vid"), m.group("coords")


def _parse_killer_info(killer_info: str) -> dict:
    killer_info = killer_info.strip()
    if not killer_info:
        return {"killer_kind": "env", "killer_name": None, "killer_steam64": None, "weapon": None}
    m = _KILLER_BOT_RE.match(killer_info)
    if m:
        return {"killer_kind": "bot", "killer_name": m.group("name"), "killer_steam64": None, "weapon": m.group("weapon")}
    m = _KILLER_PLAYER_RE.match(killer_info)
    if m:
        return {
            "killer_kind": "player", "killer_name": m.group("name"),
            "killer_steam64": int(m.group("steam64")), "weapon": m.group("weapon"),
        }
    _label, _, name = killer_info.partition(": ")
    return {"killer_kind": "env", "killer_name": name.strip() or None, "killer_steam64": None, "weapon": None}


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
        return _build_container_move_event(ts, raw, m.group("notice"), m.group("details"))

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
            return _build_item_event(ts, raw, name, steam64, verb, item_rest, verb_rest, distance)

    m = _SEL_V_RE.match(verb_rest)
    if m:
        vclass, vid, coords = _parse_vehicle_suffix(m.group("rest"))
        extra = {"verb": "сел в", "vehicle_class": vclass}
        if vid is not None:
            extra["zf_id"] = vid
        if coords is not None:
            extra["coords"] = coords
        return LogEvent(
            ts=ts, kind="vehicle_action", player=name, player_id=steam64, text=verb_rest, raw=raw,
            steam64=steam64, extra=extra,
        )

    m = _VYSHEL_RE.match(verb_rest)
    if m:
        vclass, vid, coords = _parse_vehicle_suffix(m.group("rest"))
        extra = {"verb": "вышел из", "vehicle_class": vclass}
        if vid is not None:
            extra["zf_id"] = vid
        if coords is not None:
            extra["coords"] = coords
        return LogEvent(
            ts=ts, kind="vehicle_action", player=name, player_id=steam64, text=verb_rest, raw=raw,
            steam64=steam64, extra=extra,
        )

    m = _ACTION_RE.match(verb_rest)
    if m:
        return LogEvent(
            ts=ts, kind="action", player=name, player_id=steam64, text=verb_rest, raw=raw,
            steam64=steam64, extra={"verb": "выполнил действие", "action": m.group("action")},
        )

    if verb_rest.startswith("был убит"):
        return _build_death_event(ts, raw, name, steam64, verb_rest)

    for verb in _STATUS_VERBS:
        if verb_rest.startswith(verb):
            detail = verb_rest[len(verb):].strip()
            return LogEvent(
                ts=ts, kind="status", player=name, player_id=steam64, text=verb_rest, raw=raw,
                steam64=steam64, extra={"verb": verb, "detail": detail or None},
            )

    return None


def _build_death_event(ts: datetime, raw: str, name: str, steam64: Optional[int], verb_rest: str) -> LogEvent:
    rest = verb_rest[len("был убит"):]
    m = _DEATH_RE.match(rest)
    if not m:
        return LogEvent(
            ts=ts, kind="death", player=name, player_id=steam64, text=verb_rest, raw=raw,
            steam64=steam64, extra={"tail_raw": rest.strip() or None},
        )

    killer = _parse_killer_info(m.group("killer_info"))
    pos = _parse_pos3(m.group("pos"))
    extra = {"distance": float(m.group("distance")), "cause": m.group("cause").strip(), "pos": pos, **killer}
    return LogEvent(
        ts=ts, kind="death", player=name, player_id=steam64, text=verb_rest, raw=raw,
        steam64=steam64, x=pos[0], z=pos[2], extra=extra,
    )


def _build_item_event(
    ts: datetime, raw: str, name: str, steam64: Optional[int], verb: str, item_rest: str, full_text: str,
    distance: Optional[float] = None,
) -> Optional[LogEvent]:
    m = _ITEM_RE.match(item_rest)
    if not m:
        return LogEvent(
            ts=ts, kind="item_action", player=name, player_id=steam64, text=full_text, raw=raw,
            steam64=steam64, extra={"verb": verb, "tail_raw": item_rest, "distance": distance},
        )

    item = m.group("item")
    extra = {"verb": verb, "item": item, "item_class": item, "qty": _parse_qty(m.group("qty")), "item_id": m.group("item_id")}
    if distance is not None:
        extra["distance"] = distance
    _parse_item_tail(m.group("tail"), verb, extra)

    return LogEvent(
        ts=ts, kind="item_action", player=name, player_id=steam64, text=full_text, raw=raw,
        steam64=steam64, extra=extra,
    )


def _build_container_move_event(ts: datetime, raw: str, notice: str, details: str) -> LogEvent:
    details = details.strip()
    extra = {"notice": notice.strip(), "details": details}

    item_rest = details
    matched_verb = None
    for verb in _ITEM_VERBS:
        if item_rest.startswith(verb + " "):
            matched_verb = verb
            item_rest = item_rest[len(verb) + 1:]
            break

    if matched_verb is not None:
        extra["verb"] = matched_verb
        im = _ITEM_RE.match(item_rest)
        if im:
            item = im.group("item")
            extra["item"] = item
            extra["item_class"] = item
            extra["qty"] = _parse_qty(im.group("qty"))
            extra["item_id"] = im.group("item_id")
            _parse_item_tail(im.group("tail"), matched_verb, extra)
            extra["actor_class"] = extra.get("src_root_class") or extra.get("dst_root_class")

    return LogEvent(ts=ts, kind="container_move", player=None, player_id=None, text=details, raw=raw, extra=extra)
