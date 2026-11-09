"""Интерфейс источника логов сервера. Реальные адаптеры — миссия 2."""
from __future__ import annotations

from dataclasses import dataclass
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
