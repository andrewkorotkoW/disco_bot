"""Факты из логов для промпта Claude (см. `app.ai.core.advise`).

`build_log_facts()` забирает из `LogSource` всё, что относится к тикету, и
сжимает это в `LogFacts` — набор уже отрендеренных в читаемые строки фактов
(события, окна офлайна, игроки рядом, использованный транспорт, связанные
идентичности игрока), без сырых `LogEvent`.

`resolve_time_window()` — отдельно, т.к. время окна решает вызывающий код
(`app.bot.handlers`) по `TicketExtraction.time_hint`, а не сам `build_log_facts`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from app.ai.schemas import TicketExtraction

from .base import LogEvent, LogSource

# Время в логах сервера (все парсеры app/logs/*.py) -- naive datetime в местном
# времени сервера, без tzinfo. SERVER_TZ (по умолчанию Europe/Moscow) задаёт,
# какой это часовой пояс, для справки/документации -- само значение здесь не
# используется для конвертации: вместо привязки к UTC (как было раньше и
# создавало сдвиг при сравнении с naive `ts` из SqliteLogSource) весь модуль
# работает с naive-временем в том же, "местном", соглашении, что и парсеры.
SERVER_TZ = os.environ.get("SERVER_TZ", "Europe/Moscow")

# Сколько строк фактов максимум уходит в промпт — однотипные повторы (например,
# урон от зомби) сворачиваются в одну строку со счётчиком, чтобы влезть в лимит.
MAX_EVENTS = 40

# Окно по умолчанию вокруг времени инцидента (из time_hint, либо вокруг момента
# получения тикета, если time_hint нет или не распарсился).
DEFAULT_WINDOW = timedelta(hours=2)

# Радиус поиска игроков рядом (метры) вокруг последней известной позиции игрока.
NEARBY_RADIUS = 50.0

_VEHICLE_KINDS = (
    "vehicle_open", "vehicle_close", "vehicle_action",
    "vehicle_spawn", "vehicle_despawn", "garage_store", "garage_restore",
)

_KIND_LABELS = {
    "connect": "подключение",
    "disconnect": "отключение",
    "suicide": "суицид",
    "death": "смерть",
    "bleed_out": "смерть от кровопотери",
    "respawn": "респаун",
    "unconscious": "потеря сознания",
    "chat": "сообщение в чат",
    "join": "вход на сервер",
    "leave": "выход с сервера",
    "chest_open": "открытие ящика",
    "vehicle_open": "транспорт открыт",
    "vehicle_close": "транспорт закрыт",
    "vehicle_spawn": "транспорт появился в мире",
    "vehicle_despawn": "транспорт исчез из мира",
    "garage_store": "транспорт убран в гараж",
    "garage_restore": "транспорт восстановлен из гаража",
    "container_move": "перемещение предмета между контейнерами (возможно, бот)",
    "roster": "снимок положения игрока",
    "be_connect": "подключение (BattlEye)",
    "identity": "идентификация подключения",
}


@dataclass(eq=False)
class LogFacts:
    """Факты из логов, готовые для вставки в промпт Claude."""

    events: list[str] = field(default_factory=list)
    offline_windows: list[str] = field(default_factory=list)
    nearby_players: list[str] = field(default_factory=list)
    vehicle: Optional[str] = None
    related: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.events or self.offline_windows or self.nearby_players or self.vehicle or self.related)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LogFacts):
            return (
                self.events == other.events
                and self.offline_windows == other.offline_windows
                and self.nearby_players == other.nearby_players
                and self.vehicle == other.vehicle
                and self.related == other.related
            )
        if isinstance(other, list):
            # Старый контракт log_facts (список LogEvent из NullLogSource.query())
            # считал "нет фактов" пустым списком — сохраняем это для пустых LogFacts.
            return not self and other == []
        return NotImplemented

    def to_prompt_lines(self) -> list[str]:
        """Текстовое представление «время, источник, строка» для промпта Claude."""
        if not self:
            return ["Логи: данных нет (не запрашивались либо ничего не нашлось)."]
        lines: list[str] = []
        if self.events:
            lines.append("События:")
            lines.extend(f"- {line}" for line in self.events)
        if self.offline_windows:
            lines.append("Окна офлайна:")
            lines.extend(f"- {line}" for line in self.offline_windows)
        if self.nearby_players:
            lines.append("Игроки рядом:")
            lines.extend(f"- {line}" for line in self.nearby_players)
        if self.vehicle:
            lines.append(f"Использованный транспорт: {self.vehicle}")
        if self.related:
            lines.append("Связанные идентичности игрока:")
            lines.extend(f"- {line}" for line in self.related)
        return lines

    def to_prompt_text(self) -> str:
        return "\n".join(self.to_prompt_lines())


def _format_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _event_source(event: LogEvent) -> str:
    """"Источник" строки: формат лога (adm/zf/...), если известен (SqliteLogSource
    кладёт его в raw["source"]), иначе тип события."""
    raw = event.raw
    if isinstance(raw, dict) and raw.get("source"):
        return str(raw["source"])
    return event.kind


def _event_label(event: LogEvent) -> str:
    """Короткое описание события для агрегации повторов (без чисел/координат)."""
    extra = event.extra or {}

    if event.kind == "hit":
        subtype = extra.get("subtype")
        if subtype == "infected":
            return "урон от зомби"
        if subtype == "player":
            return f"урон от игрока {event.attacker}" if event.attacker else "урон от игрока"
        if subtype == "fall":
            return "урон от падения"
        if subtype == "fire":
            return "урон от огня"
        return "урон (прочее)"

    if event.kind == "kill":
        attacker = event.attacker or "неизвестно"
        if extra.get("victim_type") == "ai":
            return f"убийство бота ({attacker})"
        return f"убийство игрока ({attacker})"

    if event.kind == "item_action":
        verb = extra.get("verb", "действие с предметом")
        item = extra.get("item")
        return f"{verb} {item}" if item else verb

    if event.kind == "vehicle_action":
        verb = extra.get("verb", "действие с транспортом")
        vclass = extra.get("vehicle_class")
        return f"{verb} {vclass}" if vclass else verb

    if event.kind == "status":
        return extra.get("verb") or "статус"

    if event.kind == "action":
        return extra.get("action") or "действие"

    if event.kind == "admin_action":
        return f"действие админа: {extra.get('subtype', 'прочее')}"

    return _KIND_LABELS.get(event.kind, event.kind)


def _event_line(event: LogEvent) -> str:
    return f"{_format_ts(event.ts)}, {_event_source(event)}, {event.text}"


def _aggregate_events(events: list[LogEvent], limit: int) -> list[str]:
    """Строки «время, источник, текст»; повторы однотипных событий (тот же
    kind + та же метка) сворачиваются в одну строку со счётчиком, если
    событий больше лимита — например «урон от зомби ×14»."""
    if len(events) <= limit:
        return [_event_line(event) for event in events]

    groups: dict[tuple[str, str], list[LogEvent]] = {}
    for event in events:
        key = (event.kind, _event_label(event))
        groups.setdefault(key, []).append(event)

    lines: list[str] = []
    for (_, label), group in groups.items():
        if len(group) == 1:
            lines.append(_event_line(group[0]))
            continue
        first, last = group[0], group[-1]
        lines.append(
            f"{_format_ts(first.ts)}..{_format_ts(last.ts)}, {_event_source(first)}, {label} ×{len(group)}"
        )
    return lines[:limit]


def _latest_position(events: list[LogEvent]) -> Optional[tuple[float, float, datetime]]:
    for event in reversed(events):
        if event.x is not None and event.z is not None:
            return event.x, event.z, event.ts
    return None


def _extract_vehicle(events: list[LogEvent]) -> Optional[str]:
    order: list[str] = []
    counts: dict[str, int] = {}
    for event in events:
        if event.kind not in _VEHICLE_KINDS:
            continue
        vclass = (event.extra or {}).get("vehicle_class")
        if not vclass:
            continue
        if vclass not in counts:
            order.append(vclass)
        counts[vclass] = counts.get(vclass, 0) + 1
    if not order:
        return None
    return "; ".join(f"{vclass} (×{counts[vclass]})" for vclass in order)


def _format_window(window: tuple[datetime, Optional[datetime]]) -> str:
    start, end = window
    end_str = _format_ts(end) if end is not None else "сейчас"
    return f"{_format_ts(start)}, offline, игрок был офлайн до {end_str}"


def _format_nearby(row: dict) -> str:
    ts = row.get("ts")
    if isinstance(ts, datetime):
        ts_str = _format_ts(ts)
    elif isinstance(ts, str):
        try:
            ts_str = _format_ts(datetime.fromisoformat(ts))
        except ValueError:
            ts_str = ts
    else:
        ts_str = "?"
    distance = row.get("distance")
    dist_str = f"{distance:.1f} м" if isinstance(distance, (int, float)) else "?"
    return f"{ts_str}, zf, рядом игрок {row.get('player')} (расстояние {dist_str})"


def _related_identities(source: LogSource, player_key: Optional[str]) -> list[str]:
    """Другие известные ники/steam64 того же игрока — через `source.resolve()`,
    если источник его реализует (сейчас — только `SqliteLogSource`)."""
    if not player_key:
        return []
    resolve = getattr(source, "resolve", None)
    if resolve is None:
        return []
    try:
        identity = resolve(player_key)
    except Exception:
        return []

    related: list[str] = []
    for name in sorted(getattr(identity, "names", None) or ()):
        if name != player_key:
            related.append(f"ник: {name}")
    for steam64 in sorted(getattr(identity, "steam64s", None) or ()):
        related.append(f"steam64: {steam64}")
    return related


def resolve_time_window(
    time_hint: Optional[str], now: Optional[datetime] = None
) -> tuple[datetime, datetime]:
    """Окно ±DEFAULT_WINDOW вокруг времени инцидента: если `time_hint` удаётся
    распарсить в дату/время — вокруг него, иначе вокруг `now` (момент получения
    тикета, т.к. в самом тикете явного времени получения нет). Naive datetime
    в местном времени сервера (SERVER_TZ) -- как и `ts` в индексе логов; не
    сравнивать с aware-datetime."""
    now = now or datetime.now()
    center = _parse_time_hint(time_hint, now) or now
    return center - DEFAULT_WINDOW, center + DEFAULT_WINDOW


_TIME_HINT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
)
_TIME_ONLY_FORMATS = ("%H:%M:%S", "%H:%M")


def _parse_time_hint(time_hint: Optional[str], now: datetime) -> Optional[datetime]:
    """Best-effort разбор time_hint в дату/время. Естественный язык («вчера
    вечером») не распознаётся — для него вызывающий код берёт дефолт `now`."""
    if not time_hint:
        return None
    text = time_hint.strip()

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        dt = None

    if dt is None:
        for fmt in _TIME_HINT_FORMATS:
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if dt is None:
        for fmt in _TIME_ONLY_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            dt = now.replace(hour=parsed.hour, minute=parsed.minute, second=parsed.second, microsecond=0)
            break

    if dt is None:
        return None
    if dt.tzinfo is not None:
        # time_hint пришёл как aware ISO-строка -- считаем, что указанное в ней
        # стенное время уже местное (SERVER_TZ), просто отбрасываем tzinfo.
        dt = dt.replace(tzinfo=None)
    return dt


def build_log_facts(
    ticket_extraction: TicketExtraction,
    source: LogSource,
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> LogFacts:
    """Собирает LogFacts по тикету. Если логи не нужны (`needs_logs=False`),
    источник не трогается вовсе и возвращаются пустые факты."""
    if not ticket_extraction.needs_logs:
        return LogFacts()

    player_key = ticket_extraction.player_name or ticket_extraction.player_id

    raw_events = source.query(player=player_key, since=since, until=until)
    events = _aggregate_events(raw_events, MAX_EVENTS)

    offline_windows: list[str] = []
    offline_fn = getattr(source, "offline_windows", None)
    if offline_fn is not None and player_key:
        try:
            offline_windows = [_format_window(window) for window in offline_fn(player_key)]
        except Exception:
            offline_windows = []

    nearby_players: list[str] = []
    nearby_fn = getattr(source, "nearby_players", None)
    position = _latest_position(raw_events)
    if nearby_fn is not None and position is not None:
        x, z, at = position
        try:
            nearby_players = [_format_nearby(row) for row in nearby_fn(x, z, NEARBY_RADIUS, at)]
        except Exception:
            nearby_players = []

    vehicle = _extract_vehicle(raw_events)
    related = _related_identities(source, player_key)

    return LogFacts(
        events=events,
        offline_windows=offline_windows,
        nearby_players=nearby_players,
        vehicle=vehicle,
        related=related,
    )
