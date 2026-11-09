"""Адаптеры логов сервера: интерфейс LogSource + заглушка NullLogSource,
индекс (SQLite), резолвер идентичностей, SqliteLogSource и сборка LogFacts
для промпта Claude."""
from .base import LogEvent, LogSource, NullLogSource
from .facts import LogFacts, build_log_facts, resolve_time_window
from .identity import Identity, resolve_identity
from .index import LogIndex
from .sqlite_source import SqliteLogSource

__all__ = [
    "LogEvent",
    "LogSource",
    "NullLogSource",
    "LogIndex",
    "Identity",
    "resolve_identity",
    "SqliteLogSource",
    "LogFacts",
    "build_log_facts",
    "resolve_time_window",
]
