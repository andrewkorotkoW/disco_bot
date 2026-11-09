"""Парсер `DayZServer_x64_<date>.ADM` — админ-лог движка (см. docs/LOGS.md, п.1).

Формат строки: `HH:MM:SS | <событие>`. Дата берётся из заголовка
`AdminLog started on YYYY-MM-DD at HH:MM:SS`; при переходе через полночь
(время строки меньше времени предыдущей строки) дата увеличивается на день.

`parse_file(path, summary=None)` — генератор `LogEvent`. Строки, которые не
удалось разобрать ни одним известным паттерном (включая заголовок, пустые
строки и служебный шум типа `[UpdateCheck] ...`), не дают события и просто
пропускаются — парсер никогда не бросает исключение из-за формата строки.
Если передать `summary` (экземпляр `ParseSummary`), в него накапливаются
счётчики: `parsed_lines` — строки, из которых получился `LogEvent`,
`skipped_lines` — все остальные непустые строки файла.

Идентификатор игрока на connect/hit/kill/chat — BI-hash (`bi_hash`), на
disconnect — steam64 (`steam64`/`player_id`).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from .base import LogEvent

logger = logging.getLogger(__name__)

_HEADER_RE = re.compile(r"^AdminLog started on (\d{4})-(\d{2})-(\d{2}) at (\d{2}):(\d{2}):(\d{2})")
_LINE_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}) \| (.*)$")

_CONNECTING_RE = re.compile(r'^Player "(?P<name>[^"]+)" \(id=(?P<id>[^ )]+)\) is connecting$')
_CONNECTED_RE = re.compile(
    r'^Player "(?P<name>[^"]+)" \(id=(?P<id>[^ )]+) pos=<(?P<pos>[^>]+)>\) is connected$'
)
_DISCONNECTED_RE = re.compile(
    r'^Player "(?P<name>[^"]+)" \(id=(?P<id>[^ )]+) pos=<(?P<pos>[^>]+)>\) has been disconnected$'
)
_HIT_RE = re.compile(
    r'^Player "(?P<name>[^"]+)" \(id=(?P<id>[^ )]+)'
    r'(?: group=(?P<group>\d+))?(?: faction="(?P<faction>[^"]*)")? pos=<(?P<pos>[^>]+)>\)'
    r"\[HP: (?P<hp>[\d.]+)\] hit by (?P<rest>.+)$"
)
_SUICIDE_RE = re.compile(r'^Player "(?P<name>[^"]+)" \(id=(?P<id>[^)]+?)\s*\) committed suicide$')
_KILLED_PLAYER_RE = re.compile(
    r'^Player "(?P<name>[^"]+)" \(DEAD\) \(id=(?P<id>[^ )]+)\) killed by (?P<rest>.+)$'
)
_KILLED_AI_RE = re.compile(
    r'^AI "(?P<name>[^"]+)" \(DEAD\) \(group=(?P<group>\d+):"(?P<gname>[^"]*)" '
    r'faction="(?P<faction>[^"]*)" pos=<(?P<pos>[^>]+)>\) killed by (?P<rest>.+)$'
)
_DEATH_RE = re.compile(r'^Player "(?P<name>[^"]+)" \(DEAD\) \(id=(?P<id>[^ )]+)\) died\. Stats> (?P<stats>.+)$')
_BLED_OUT_RE = re.compile(r'^Player "(?P<name>[^"]+)" \(DEAD\) \(id=(?P<id>[^ )]+)\) bled out$')
_RESPAWN_RE = re.compile(r'^Player "(?P<name>[^"]+)" \(DEAD\) \(id=(?P<id>[^ )]+)\) is choosing to respawn$')
_AI_STATE_RE = re.compile(
    r'^AI "(?P<name>[^"]+)" \(group=(?P<group>\d+):"(?P<gname>[^"]*)" faction="(?P<faction>[^"]*)" '
    r"pos=<(?P<pos>[^>]+)>\) is (?P<state>unconscious|regained consciousness|turned hostile to .+)$"
)
_CHAT_RE = re.compile(r'^\[Chat - (?P<channel>[^\]]+)\]\("(?P<name>[^"]+)"\(id=(?P<id>[^)]+)\)\): (?P<text>.*)$')

_ATTACKER_PLAYER_RE = re.compile(
    r'^Player "(?P<aname>[^"]+)" \(id=(?P<aid>[^ )]+)'
    r'(?: group=(?P<agroup>\d+))?(?: faction="(?P<afaction>[^"]*)")?(?: pos=<[^>]+>)?\)\s*(?P<tail>.*)$'
)
_TAIL_RE = re.compile(
    r"^into (?P<body_part>\w+)\((?P<bp_num>\d+)\) for (?P<damage>[\d.]+) damage"
    r"(?: \((?P<weapon>[^)]+)\))?$|^with (?P<weapon2>.+?) from (?P<distance>[\d.]+)$"
)
_INFECTED_HIT_RE = re.compile(
    r"^(?P<aname>Зараженный) (?P<tail>into \w+\(\d+\) for [\d.]+ damage(?: \([^)]+\))?)$"
)
_FIRE_HIT_RE = re.compile(r"^(?P<weapon>.+?) with (?P<dtype>\w+)$")


def _steam64(raw_id: str) -> Optional[int]:
    return int(raw_id) if raw_id.isdigit() else None


def _parse_pos(pos: str) -> tuple[Optional[float], Optional[float]]:
    """ADM: pos=<x, z, y> — третье значение (высота) не храним в top-level полях."""
    parts = [p.strip() for p in pos.split(",")]
    try:
        x = float(parts[0])
        z = float(parts[1])
        return x, z
    except (ValueError, IndexError):
        return None, None


def _parse_hit_tail(rest: str) -> dict:
    """Best-effort разбор 'hit by ...' — не критично для создания события hit."""
    info: dict = {"subtype": "other", "attacker": rest}
    m = _ATTACKER_PLAYER_RE.match(rest)
    if m:
        info["subtype"] = "player"
        info["attacker"] = m.group("aname")
        info["attacker_id"] = m.group("aid")
        tail = m.group("tail")
        tm = _TAIL_RE.match(tail)
        if tm:
            if tm.group("body_part"):
                info["body_part"] = tm.group("body_part")
                info["damage"] = float(tm.group("damage"))
                info["weapon"] = tm.group("weapon")
            else:
                info["weapon"] = tm.group("weapon2")
                info["distance"] = float(tm.group("distance"))
        return info
    m = _INFECTED_HIT_RE.match(rest)
    if m:
        info["subtype"] = "infected"
        info["attacker"] = m.group("aname")
        tm = _TAIL_RE.match(m.group("tail"))
        if tm and tm.group("body_part"):
            info["body_part"] = tm.group("body_part")
            info["damage"] = float(tm.group("damage"))
            info["weapon"] = tm.group("weapon")
        return info
    if rest.startswith("FallDamageHealth"):
        info["subtype"] = "fall"
        info["attacker"] = "FallDamageHealth"
        return info
    m = _FIRE_HIT_RE.match(rest)
    if m and "FireDamage" in rest:
        info["subtype"] = "fire"
        info["attacker"] = m.group("weapon")
        info["weapon"] = m.group("weapon")
        return info
    return info


def _parse_kill_rest(rest: str) -> dict:
    info: dict = {"attacker": rest}
    m = _ATTACKER_PLAYER_RE.match(rest)
    if m:
        info["attacker"] = m.group("aname")
        info["attacker_id"] = m.group("aid")
        tail = m.group("tail")
        if tail:
            tm = _TAIL_RE.match(tail)
            if tm and tm.group("weapon2"):
                info["weapon"] = tm.group("weapon2")
                info["distance"] = float(tm.group("distance"))
    return info


@dataclass
class ParseSummary:
    parsed_lines: int = 0
    skipped_lines: int = 0


def parse_file(path: str | Path, summary: Optional[ParseSummary] = None) -> Iterator[LogEvent]:
    current_date: Optional[date] = None
    prev_time: Optional[tuple[int, int, int]] = None

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n\r")
            if not line.strip():
                continue

            header_m = _HEADER_RE.match(line)
            if header_m:
                y, mo, d, h, mi, s = (int(v) for v in header_m.groups())
                current_date = date(y, mo, d)
                prev_time = (h, mi, s)
                continue

            line_m = _LINE_RE.match(line)
            if not line_m or current_date is None:
                if summary is not None:
                    summary.skipped_lines += 1
                logger.debug("adm: unparsed line: %r", line)
                continue

            h, mi, s, body = line_m.groups()
            h, mi, s = int(h), int(mi), int(s)
            if prev_time is not None and (h, mi, s) < prev_time:
                current_date += timedelta(days=1)
            prev_time = (h, mi, s)
            ts = datetime(current_date.year, current_date.month, current_date.day, h, mi, s)

            event = _parse_body(ts, body, raw=line)
            if event is None:
                if summary is not None:
                    summary.skipped_lines += 1
                logger.debug("adm: unparsed body: %r", body)
                continue
            if summary is not None:
                summary.parsed_lines += 1
            yield event


def _parse_body(ts: datetime, body: str, raw: str) -> Optional[LogEvent]:
    if _CONNECTING_RE.match(body):
        return None

    m = _CONNECTED_RE.match(body)
    if m:
        x, z = _parse_pos(m.group("pos"))
        return LogEvent(
            ts=ts, kind="connect", player=m.group("name"), player_id=None, text=body, raw=raw,
            bi_hash=m.group("id"), x=x, z=z,
        )

    m = _DISCONNECTED_RE.match(body)
    if m:
        steam64 = _steam64(m.group("id"))
        return LogEvent(
            ts=ts, kind="disconnect", player=m.group("name"), player_id=steam64, text=body, raw=raw,
            steam64=steam64,
        )

    m = _HIT_RE.match(body)
    if m:
        x, z = _parse_pos(m.group("pos"))
        info = _parse_hit_tail(m.group("rest"))
        return LogEvent(
            ts=ts, kind="hit", player=m.group("name"), player_id=None, text=body, raw=raw,
            bi_hash=m.group("id"), group=m.group("group"), faction=m.group("faction"),
            hp=float(m.group("hp")), x=x, z=z,
            attacker=info.get("attacker"), weapon=info.get("weapon"),
            distance=info.get("distance"), body_part=info.get("body_part"), damage=info.get("damage"),
            extra={"subtype": info.get("subtype"), "attacker_id": info.get("attacker_id")},
        )

    m = _SUICIDE_RE.match(body)
    if m:
        return LogEvent(
            ts=ts, kind="suicide", player=m.group("name"), player_id=None, text=body, raw=raw,
            bi_hash=m.group("id"),
        )

    m = _KILLED_PLAYER_RE.match(body)
    if m:
        info = _parse_kill_rest(m.group("rest"))
        return LogEvent(
            ts=ts, kind="kill", player=m.group("name"), player_id=None, text=body, raw=raw,
            bi_hash=m.group("id"), attacker=info.get("attacker"), weapon=info.get("weapon"),
            distance=info.get("distance"),
            extra={"victim_type": "player", "attacker_id": info.get("attacker_id")},
        )

    m = _KILLED_AI_RE.match(body)
    if m:
        info = _parse_kill_rest(m.group("rest"))
        return LogEvent(
            ts=ts, kind="kill", player=None, player_id=None, text=body, raw=raw,
            group=m.group("group"), faction=m.group("faction"),
            attacker=info.get("attacker"), weapon=info.get("weapon"), distance=info.get("distance"),
            extra={"victim_type": "ai", "victim_name": m.group("name"), "attacker_id": info.get("attacker_id")},
        )

    m = _DEATH_RE.match(body)
    if m:
        return LogEvent(
            ts=ts, kind="death", player=m.group("name"), player_id=None, text=body, raw=raw,
            bi_hash=m.group("id"), extra={"stats": m.group("stats")},
        )

    m = _BLED_OUT_RE.match(body)
    if m:
        return LogEvent(
            ts=ts, kind="bleed_out", player=m.group("name"), player_id=None, text=body, raw=raw,
            bi_hash=m.group("id"),
        )

    m = _RESPAWN_RE.match(body)
    if m:
        return LogEvent(
            ts=ts, kind="respawn", player=m.group("name"), player_id=None, text=body, raw=raw,
            bi_hash=m.group("id"),
        )

    m = _AI_STATE_RE.match(body)
    if m:
        x, z = _parse_pos(m.group("pos"))
        return LogEvent(
            ts=ts, kind="unconscious", player=None, player_id=None, text=body, raw=raw,
            group=m.group("group"), faction=m.group("faction"), x=x, z=z,
            extra={"ai_name": m.group("name"), "state": m.group("state")},
        )

    m = _CHAT_RE.match(body)
    if m:
        return LogEvent(
            ts=ts, kind="chat", player=m.group("name"), player_id=None, text=m.group("text"), raw=raw,
            bi_hash=m.group("id"), extra={"channel": m.group("channel")},
        )

    return None
