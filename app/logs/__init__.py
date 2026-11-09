"""Адаптеры логов сервера: интерфейс LogSource + заглушка NullLogSource,
индекс (SQLite), резолвер идентичностей и SqliteLogSource."""
from .base import LogEvent, LogSource, NullLogSource
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
]
