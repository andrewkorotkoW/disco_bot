"""Обёртка над Claude API: extract_ticket, advise.

Структурированный вывод получаем через client.messages.parse() с
pydantic-моделями из app.ai.schemas — без prefill ассистента и без
параметра thinking (на текущих моделях это даёт лимит в 400 токенов).
"""
from __future__ import annotations

import json
from typing import Any

from anthropic import Anthropic
from anthropic.types import Message

from app.ai.client import MAX_TOKENS, get_client, get_model
from app.ai.exceptions import ClaudeRefusalError
from app.ai.prompts import ADVISE_SYSTEM_PROMPT, EXTRACT_TICKET_SYSTEM_PROMPT
from app.ai.schemas import AdviceResponse, TicketExtraction


def _check_not_refused(response: Message) -> None:
    if response.stop_reason == "refusal":
        raise ClaudeRefusalError(
            "Claude отказался отвечать на запрос (stop_reason='refusal'); "
            "содержимое ответа не несёт структурированного результата."
        )


def _dump(value: Any) -> str:
    """Сериализует значение (dict/list/pydantic-модель/примитив) для промпта."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def extract_ticket(text: str, *, client: Anthropic | None = None) -> TicketExtraction:
    """Извлекает из текста тикета игрока структурированные поля."""
    client = client or get_client()
    response = client.messages.parse(
        model=get_model(),
        max_tokens=MAX_TOKENS,
        system=EXTRACT_TICKET_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
        output_format=TicketExtraction,
    )
    _check_not_refused(response)
    return response.parsed_output


def advise(
    ticket: Any,
    similar_cases: Any,
    log_facts: Any,
    *,
    client: Anthropic | None = None,
) -> AdviceResponse:
    """Собирает разбор тикета для админа: факты, похожие кейсы, рекомендацию."""
    client = client or get_client()
    user_content = (
        "Тикет администратора:\n" + _dump(ticket) + "\n\n"
        "Похожие кейсы из базы:\n" + _dump(similar_cases) + "\n\n"
        "Факты из логов сервера:\n" + _dump(log_facts)
    )
    response = client.messages.parse(
        model=get_model(),
        max_tokens=MAX_TOKENS,
        system=ADVISE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        output_format=AdviceResponse,
    )
    _check_not_refused(response)
    return response.parsed_output
