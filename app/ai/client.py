"""Фабрика клиента anthropic для app.ai.

Клиент не создаётся на уровне импорта модуля (это требует сети/валидного
ключа) — он лениво создаётся при первом вызове get_client() и кэшируется.
Для тестов клиент можно подменить через set_client(), минуя создание
настоящего anthropic.Anthropic().
"""
from __future__ import annotations

import os

import anthropic

DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 4000

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _client


def set_client(client: anthropic.Anthropic | None) -> None:
    """Подменяет кэшированный клиент (используется в тестах)."""
    global _client
    _client = client


def get_model() -> str:
    return os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL
