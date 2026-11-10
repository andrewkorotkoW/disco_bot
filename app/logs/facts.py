"""Факты из логов для промпта Claude (см. `app.ai.core.advise`).

`build_log_facts()` забирает из `LogSource` всё, что относится к тикету и
сжимает это в `LogFacts` — факты, уже отрендеренные в читаемые строки. Набор
собираемых фактов зависит от категории тикета (`TicketExtraction.category`):

- `loot_loss` — история машины/контейнера, доступ посторонних к ней, кто был
  рядом в окна офлайна владельца, последние действия владельца с ней и смерти
  владельца (с возможными подборами лута ботами у трупа).
- `cheat`/`dispute` — PvP-урон/убийства между игроками (дистанция, оружие).
- всё остальное (`bug`/`other`/`ban_appeal`) — общий таймлайн событий игрока,
  как раньше.

`resolve_time_window()` — отдельно, т.к. время окна решает вызывающий код
(`app.bot.handlers`) по `TicketExtraction.time_hint`, а не сам `build_log_facts`.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from app.ai.schemas import TicketExtraction

from .base import LogEvent, LogSource
from .identity import Identity

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

# Радиус поиска игроков рядом (метры) вокруг последней известной позиции игрока
# либо машины/контейнера из тикета о пропаже лута.
NEARBY_RADIUS = 50.0

# Сколько последних действий владельца с машиной/контейнером показываем в
# loot_loss-тикете (без агрегации — каждое действие своей строкой).
OWNER_ACTIONS_LIMIT = 20

# Окно и радиус, в которых подбор предмета (container_move) после смерти
# владельца считается "подбором у трупа" (loot_loss, п.г).
DEATH_PICKUP_WINDOW = timedelta(minutes=10)
DEATH_PICKUP_RADIUS = 10.0

_VEHICLE_KINDS = (
    "vehicle_open", "vehicle_close", "vehicle_action",
    "vehicle_spawn", "vehicle_despawn", "garage_store", "garage_restore",
)
_VEHICLE_ACCESS_KINDS = (
    "vehicle_open", "vehicle_close", "vehicle_action", "garage_store", "garage_restore",
)
_CONTAINER_CLASS_KEYS = ("vehicle_class", "item_class", "src_root_class", "dst_root_class")
_SELF_CONTAINER_CLASSES = {"Персонаж", "Character"}
_BOT_ACTOR_RE = re.compile(r"(?i)zmb|infected|\bai\b|eai|bot")

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

    # Общий таймлайн (bug/other/ban_appeal) и метаданные, использовавшиеся до
    # разделения по категориям тикета -- оставлены для обратной совместимости
    # контракта (см. app/ai/core.py, app/bot/handlers.py: рендерят LogFacts
    # через to_prompt_text(), но старый код мог заглядывать в эти поля).
    events: list[str] = field(default_factory=list)
    offline_windows: list[str] = field(default_factory=list)
    nearby_players: list[str] = field(default_factory=list)
    vehicle: Optional[str] = None
    related: list[str] = field(default_factory=list)

    # loot_loss: история машины/контейнера и всё, что к ней относится.
    vehicle_history: list[str] = field(default_factory=list)
    others_access: list[str] = field(default_factory=list)
    offline_nearby: list[str] = field(default_factory=list)
    owner_actions: list[str] = field(default_factory=list)
    deaths: list[str] = field(default_factory=list)

    # Метаданные для рендеринга (не сами факты) -- категория тикета и класс
    # машины/контейнера, который удалось определить для loot_loss.
    category: Optional[str] = None
    target_class: Optional[str] = None

    def __bool__(self) -> bool:
        return bool(
            self.events or self.offline_windows or self.nearby_players or self.vehicle or self.related
            or self.vehicle_history or self.others_access or self.offline_nearby
            or self.owner_actions or self.deaths
        )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LogFacts):
            return (
                self.events == other.events
                and self.offline_windows == other.offline_windows
                and self.nearby_players == other.nearby_players
                and self.vehicle == other.vehicle
                and self.related == other.related
                and self.vehicle_history == other.vehicle_history
                and self.others_access == other.others_access
                and self.offline_nearby == other.offline_nearby
                and self.owner_actions == other.owner_actions
                and self.deaths == other.deaths
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
        if self.vehicle_history:
            header = "История транспорта/контейнера"
            if self.target_class:
                header += f" ({self.target_class})"
            lines.append(header + ":")
            lines.extend(f"- {line}" for line in self.vehicle_history)
        if self.others_access:
            lines.append("Кто кроме владельца открывал/брал/клал:")
            lines.extend(f"- {line}" for line in self.others_access)
        elif self.target_class:
            # Главный факт для loot_loss, подтверждающий пропажу "без следов" —
            # должен быть явной строкой, а не просто пропущенным разделом.
            lines.append(f"Других игроков, трогавших {self.target_class}, в логах нет.")
        if self.owner_actions:
            lines.append("Последние действия владельца с машиной/контейнером:")
            lines.extend(f"- {line}" for line in self.owner_actions)
        if self.offline_nearby:
            lines.append("Игроки рядом в окна офлайна владельца:")
            lines.extend(f"- {line}" for line in self.offline_nearby)
        if self.deaths:
            lines.append("Смерти владельца и подборы у трупа:")
            lines.extend(f"- {line}" for line in self.deaths)
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
        item = extra.get("item_class") or extra.get("item")
        return f"{verb} {item}" if item else verb

    if event.kind == "container_move":
        verb = extra.get("verb", "действие с предметом")
        item = extra.get("item_class") or extra.get("item")
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
    kind + та же метка — на практике пара «глагол, класс предмета/машины»)
    сворачиваются в одну строку со счётчиком, если событий больше лимита —
    например «урон от зомби ×14»."""
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


def _extra_pos_xz(event: LogEvent) -> Optional[tuple[float, float]]:
    """x/z из extra['pos'] -- строка/тройка «x, y(высота), z» (zf/carcodelock),
    когда top-level x/z на событии не заполнены."""
    pos = (event.extra or {}).get("pos")
    if isinstance(pos, (list, tuple)) and len(pos) >= 3 and pos[0] is not None and pos[2] is not None:
        try:
            return float(pos[0]), float(pos[2])
        except (TypeError, ValueError):
            return None
    return None


def _last_known_pos(events: list[LogEvent]) -> Optional[tuple[float, float]]:
    for event in sorted(events, key=lambda e: e.ts, reverse=True):
        if event.x is not None and event.z is not None:
            return event.x, event.z
        pos = _extra_pos_xz(event)
        if pos is not None:
            return pos
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


def _format_offline_nearby(row: dict) -> str:
    ts = row.get("checked_at") or row.get("ts")
    if isinstance(ts, datetime):
        ts_str = _format_ts(ts)
    else:
        ts_str = str(ts) if ts is not None else "?"
    distance = row.get("distance")
    dist_str = f"{distance:.1f} м" if isinstance(distance, (int, float)) else "?"
    return (
        f"{ts_str}, zf, рядом игрок {row.get('player')} (расстояние {dist_str}), "
        "пока владелец был офлайн"
    )


def _resolve_identity_safe(source: LogSource, player_key: Optional[str]) -> Identity:
    if not player_key:
        return Identity()
    resolve = getattr(source, "resolve", None)
    if resolve is None:
        return Identity()
    try:
        return resolve(player_key)
    except Exception:
        return Identity()


def _related_identities(identity: Identity, player_key: Optional[str]) -> list[str]:
    """Другие известные ники/steam64 того же игрока — из `Identity`, полученной
    через `source.resolve()`, если источник его реализует (сейчас — только
    `SqliteLogSource`)."""
    related: list[str] = []
    for name in sorted(identity.names or ()):
        if name != player_key:
            related.append(f"ник: {name}")
    for steam64 in sorted(identity.steam64s or ()):
        related.append(f"steam64: {steam64}")
    return related


def _is_owner_event(event: LogEvent, names: set, steam64s: set) -> bool:
    if event.player and event.player in names:
        return True
    if event.steam64 is not None and event.steam64 in steam64s:
        return True
    return False


def _relates_to_class(event: LogEvent, target_class: str) -> bool:
    extra = event.extra or {}
    return any(extra.get(key) == target_class for key in _CONTAINER_CLASS_KEYS)


def _candidate_classes(events: list[LogEvent]) -> set:
    classes: set = set()
    for event in events:
        extra = event.extra or {}
        for key in _CONTAINER_CLASS_KEYS:
            value = extra.get(key)
            if value and value not in _SELF_CONTAINER_CLASSES:
                classes.add(value)
    return classes


def _latest_class(events: list[LogEvent]) -> Optional[str]:
    """Последняя машина/контейнер, с которой(ым) взаимодействовал владелец —
    запасной вариант, когда класс явно не назван в тексте тикета."""
    by_ts_desc = sorted(events, key=lambda e: e.ts, reverse=True)
    for event in by_ts_desc:
        if event.kind in _VEHICLE_KINDS:
            vclass = (event.extra or {}).get("vehicle_class")
            if vclass:
                return vclass
    for event in by_ts_desc:
        if event.kind in ("item_action", "container_move"):
            extra = event.extra or {}
            root = extra.get("dst_root_class") or extra.get("src_root_class")
            if root and root not in _SELF_CONTAINER_CLASSES:
                return root
    return None


def _find_target_class(summary: Optional[str], owner_events: list[LogEvent]) -> Optional[str]:
    """Класс машины/контейнера из тикета о пропаже лута: явное упоминание в
    summary тикета (по классам, с которыми владелец реально взаимодействовал),
    иначе — последняя машина/контейнер владельца."""
    text = (summary or "").lower()
    candidates = _candidate_classes(owner_events)
    mentioned = [cls for cls in candidates if cls.lower() in text]
    if mentioned:
        return max(mentioned, key=len)
    return _latest_class(owner_events)


def _looks_like_bot_actor(actor_class: Optional[str]) -> bool:
    if not actor_class:
        # Нет данных об акторе (типичный случай -- строка-маркер "!!!предмет
        # был перемещён..." без цепочки контейнеров) -- не можем исключить
        # бота, поэтому не отфильтровываем.
        return True
    return bool(_BOT_ACTOR_RE.search(actor_class))


def _bot_pickups_near_death(source: LogSource, death: LogEvent) -> list[str]:
    """container_move в первые DEATH_PICKUP_WINDOW минут после смерти, в
    радиусе DEATH_PICKUP_RADIUS м от места смерти и без признаков того, что
    актор -- игрок (нет данных об акторе либо класс похож на бота/ИИ)."""
    since = death.ts
    until = death.ts + DEATH_PICKUP_WINDOW
    try:
        candidates = source.query(since=since, until=until, kinds=("container_move",))
    except Exception:
        return []

    death_pos: Optional[tuple[float, float]] = None
    if death.x is not None and death.z is not None:
        death_pos = (death.x, death.z)
    else:
        death_pos = _extra_pos_xz(death)

    lines: list[str] = []
    for event in sorted(candidates, key=lambda e: e.ts):
        extra = event.extra or {}
        if not _looks_like_bot_actor(extra.get("actor_class")):
            continue
        if death_pos is not None:
            candidate_pos = _extra_pos_xz(event)
            if candidate_pos is not None:
                distance = math.hypot(candidate_pos[0] - death_pos[0], candidate_pos[1] - death_pos[1])
                if distance > DEATH_PICKUP_RADIUS:
                    continue
        lines.append(f"{_format_ts(event.ts)}, zf, подбор у трупа (возможно бот/ИИ): {event.text}")
    return lines


def _pvp_event_involves_player(event: LogEvent, names: set, steam64s: set) -> bool:
    if event.player and event.player in names:
        return True
    if event.attacker and event.attacker in names:
        return True
    extra = event.extra or {}
    attacker_id = extra.get("attacker_id")
    if attacker_id is not None:
        try:
            if int(attacker_id) in steam64s:
                return True
        except (TypeError, ValueError):
            pass
    if event.steam64 is not None and event.steam64 in steam64s:
        return True
    return False


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


def _build_timeline_facts(
    raw_events: list[LogEvent], source: LogSource, player_key: Optional[str], related: list[str]
) -> LogFacts:
    """bug/other/ban_appeal и всё, что не попало под более специфичную
    категорию: общий таймлайн действий игрока, как раньше."""
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

    return LogFacts(
        events=events,
        offline_windows=offline_windows,
        nearby_players=nearby_players,
        vehicle=vehicle,
        related=related,
    )


def _build_pvp_facts(
    source: LogSource,
    player_key: Optional[str],
    since: Optional[datetime],
    until: Optional[datetime],
    related: list[str],
    identity: Identity,
    category: str,
) -> LogFacts:
    """cheat/dispute: урон и убийства между игроками (attacker_kind='player'),
    с дистанцией и оружием -- оба уже есть в `event.text`/полях `LogEvent`."""
    names = set(identity.names or ()) | ({player_key} if player_key else set())
    steam64s = set(identity.steam64s or ())

    try:
        candidates = source.query(since=since, until=until, kinds=("hit", "kill"))
    except Exception:
        candidates = []

    pvp_events = [
        event
        for event in candidates
        if (event.extra or {}).get("attacker_kind") == "player"
        and _pvp_event_involves_player(event, names, steam64s)
    ]
    pvp_events.sort(key=lambda e: e.ts)

    return LogFacts(events=_aggregate_events(pvp_events, MAX_EVENTS), related=related, category=category)


def _build_loot_loss_facts(
    source: LogSource,
    player_key: Optional[str],
    raw_events: list[LogEvent],
    ticket_extraction: TicketExtraction,
    since: Optional[datetime],
    until: Optional[datetime],
    related: list[str],
    identity: Identity,
) -> LogFacts:
    """loot_loss: история машины/контейнера, доступ посторонних, кто был рядом
    в окна офлайна владельца, последние действия владельца и его смерти."""
    target_class = _find_target_class(ticket_extraction.summary, raw_events)

    vehicle_history_events: list[LogEvent] = []
    container_events: list[LogEvent] = []
    others_access_events: list[LogEvent] = []
    owner_actions_events: list[LogEvent] = []
    target_pos: Optional[tuple[float, float]] = None

    if target_class:
        vehicle_history_fn = getattr(source, "vehicle_history", None)
        if vehicle_history_fn is not None:
            try:
                vehicle_history_events = list(vehicle_history_fn(target_class, until=until))
            except Exception:
                vehicle_history_events = []

        container_access_fn = getattr(source, "container_access", None)
        if container_access_fn is not None:
            try:
                container_events = list(container_access_fn(target_class, until=until))
            except Exception:
                container_events = []

        names = set(identity.names or ()) | ({player_key} if player_key else set())
        steam64s = set(identity.steam64s or ())

        access_events = [e for e in vehicle_history_events if e.kind in _VEHICLE_ACCESS_KINDS] + container_events
        others_access_events = sorted(
            (e for e in access_events if not _is_owner_event(e, names, steam64s)),
            key=lambda e: e.ts,
        )

        target_pos = _last_known_pos(vehicle_history_events + container_events)

        owner_actions_events = sorted(
            (e for e in raw_events if _relates_to_class(e, target_class)), key=lambda e: e.ts
        )

    offline_nearby_rows: list[dict] = []
    if target_pos is not None and player_key:
        nearby_during_offline_fn = getattr(source, "nearby_during_offline", None)
        if nearby_during_offline_fn is not None:
            try:
                offline_nearby_rows = nearby_during_offline_fn(
                    player_key, target_pos, NEARBY_RADIUS, since=since, until=until
                )
            except Exception:
                offline_nearby_rows = []

    deaths: list[str] = []
    for death in sorted((e for e in raw_events if e.kind == "death"), key=lambda e: e.ts):
        deaths.append(_event_line(death))
        deaths.extend(_bot_pickups_near_death(source, death))

    return LogFacts(
        vehicle_history=_aggregate_events(sorted(vehicle_history_events, key=lambda e: e.ts), MAX_EVENTS),
        others_access=_aggregate_events(others_access_events, MAX_EVENTS),
        offline_nearby=[_format_offline_nearby(row) for row in offline_nearby_rows],
        owner_actions=[_event_line(e) for e in owner_actions_events[-OWNER_ACTIONS_LIMIT:]],
        deaths=deaths,
        related=related,
        category="loot_loss",
        target_class=target_class,
    )


def build_log_facts(
    ticket_extraction: TicketExtraction,
    source: LogSource,
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> LogFacts:
    """Собирает LogFacts по тикету. Если логи не нужны (`needs_logs=False`),
    источник не трогается вовсе и возвращаются пустые факты. Набор собираемых
    фактов зависит от `ticket_extraction.category` (см. докстринг модуля)."""
    if not ticket_extraction.needs_logs:
        return LogFacts()

    player_key = ticket_extraction.player_name or ticket_extraction.player_id
    identity = _resolve_identity_safe(source, player_key)
    related = _related_identities(identity, player_key)

    raw_events = source.query(player=player_key, since=since, until=until)

    category = ticket_extraction.category
    if category == "loot_loss":
        return _build_loot_loss_facts(
            source, player_key, raw_events, ticket_extraction, since, until, related, identity
        )
    if category in ("cheat", "dispute"):
        return _build_pvp_facts(source, player_key, since, until, related, identity, category)
    return _build_timeline_facts(raw_events, source, player_key, related)
