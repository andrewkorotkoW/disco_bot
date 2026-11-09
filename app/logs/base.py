"""Интерфейс источника логов сервера. Реальные адаптеры — миссия 2."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol, Sequence


@dataclass
class LogEvent:
    ts: datetime
    kind: str
    player: Optional[str]
    player_id: Optional[int]
    text: str
    raw: Any
    # Поля ниже — расширение под парсеры пяти форматов логов (см. docs/LOGS.md).
    # Все с default=None, чтобы не ломать существующие вызовы LogEvent(...).
    bi_hash: Optional[str] = None
    steam64: Optional[int] = None
    group: Optional[str] = None
    faction: Optional[str] = None
    x: Optional[float] = None
    z: Optional[float] = None
    hp: Optional[float] = None
    attacker: Optional[str] = None
    weapon: Optional[str] = None
    distance: Optional[float] = None
    body_part: Optional[str] = None
    damage: Optional[float] = None
    extra: Optional[dict] = field(default=None)


class LogSource(Protocol):
    def query(
        self,
        player: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        kinds: Optional[Sequence[str]] = None,
    ) -> list[LogEvent]:
        ...


class NullLogSource:
    """Заглушка: источник логов ещё не подключён (ждёт образец логов, миссия 2)."""

    def query(
        self,
        player: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        kinds: Optional[Sequence[str]] = None,
    ) -> list[LogEvent]:
        return []
