"""Адаптеры логов сервера: интерфейс LogSource + заглушка NullLogSource."""
from .base import LogEvent, LogSource, NullLogSource

__all__ = ["LogEvent", "LogSource", "NullLogSource"]
