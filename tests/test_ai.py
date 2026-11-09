"""Тесты app.ai: extract_ticket/advise на фейковом anthropic-клиенте.

Клиент подменяется через monkeypatch (app.ai.core.get_client), поэтому
client.messages.parse() никогда не уходит в сеть и не требует
ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import pytest

from app.ai import core as ai_core
from app.ai.exceptions import ClaudeRefusalError
from app.ai.schemas import AdviceResponse, TicketExtraction


class FakeMessages:
    """Подмена client.messages: parse() просто отдаёт заготовленный ответ."""

    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


class FakeResponse:
    """Фейковый ответ happy-path: stop_reason обычный, parsed_output готов."""

    def __init__(self, parsed_output, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self._parsed_output = parsed_output

    @property
    def parsed_output(self):
        return self._parsed_output


class RefusalResponse:
    """Фейковый ответ при отказе модели.

    content и parsed_output оба "взрывоопасны" — если код полезет туда
    читать результат до проверки stop_reason, тест упадёт с AssertionError
    вместо ожидаемого ClaudeRefusalError.
    """

    stop_reason = "refusal"

    @property
    def content(self):
        raise AssertionError("content не должен читаться при stop_reason='refusal'")

    @property
    def parsed_output(self):
        raise AssertionError("parsed_output не должен читаться при stop_reason='refusal'")


def _install_fake_client(monkeypatch, response) -> FakeClient:
    fake_client = FakeClient(response)
    monkeypatch.setattr(ai_core, "get_client", lambda: fake_client)
    return fake_client


def test_extract_ticket_happy_path(monkeypatch):
    expected = TicketExtraction(
        player_name="SnezhniyBars",
        player_id="900000000000000001",
        category="cheat",
        summary="Жалоба на вскрытие стен при штурме базы.",
        time_hint="вчера вечером",
        needs_logs=True,
    )
    fake_client = _install_fake_client(monkeypatch, FakeResponse(expected))

    result = ai_core.extract_ticket("Залетел на базу, а он через стену меня застрелил.")

    assert result is expected
    assert len(fake_client.messages.calls) == 1
    call = fake_client.messages.calls[0]
    assert call["messages"] == [
        {"role": "user", "content": "Залетел на базу, а он через стену меня застрелил."}
    ]
    assert call["system"] == ai_core.EXTRACT_TICKET_SYSTEM_PROMPT
    assert call["output_format"] is TicketExtraction
    assert call["model"] == ai_core.get_model()


def test_advise_happy_path(monkeypatch):
    expected = AdviceResponse(
        summary="Подозрение на вскрытие стен.",
        facts=["логи не предоставлены"],
        similar=["кейс #1: перманентный бан за вскрытие стен"],
        recommendation="Забанить до выяснения, поднять логи позиций.",
        confidence=0.4,
        open_questions=["нет данных по позициям игрока за последний час"],
    )
    fake_client = _install_fake_client(monkeypatch, FakeResponse(expected))

    ticket = {"category": "cheat", "summary": "Жалоба на вскрытие стен."}
    similar_cases = [{"case_id": 1, "decision": "перманентный бан"}]
    log_facts: list[str] = []

    result = ai_core.advise(ticket, similar_cases, log_facts)

    assert result is expected
    call = fake_client.messages.calls[0]
    assert call["system"] == ai_core.ADVISE_SYSTEM_PROMPT
    assert call["output_format"] is AdviceResponse
    user_content = call["messages"][0]["content"]
    assert "Жалоба на вскрытие стен." in user_content
    assert "перманентный бан" in user_content


@pytest.mark.parametrize(
    "call_fn",
    [
        lambda: ai_core.extract_ticket("текст тикета"),
        lambda: ai_core.advise({"a": 1}, [], []),
    ],
    ids=["extract_ticket", "advise"],
)
def test_refusal_raises_before_reading_response(monkeypatch, call_fn):
    _install_fake_client(monkeypatch, RefusalResponse())

    with pytest.raises(ClaudeRefusalError):
        call_fn()


@pytest.mark.parametrize(
    "call_fn",
    [
        lambda: ai_core.extract_ticket("текст тикета"),
        lambda: ai_core.advise({"a": 1}, [], []),
    ],
    ids=["extract_ticket", "advise"],
)
def test_request_has_no_thinking_and_enough_max_tokens(monkeypatch, call_fn):
    parsed = TicketExtraction(category="other", summary="x", needs_logs=False)
    fake_client = _install_fake_client(monkeypatch, FakeResponse(parsed))

    call_fn()

    call = fake_client.messages.calls[0]
    assert "thinking" not in call
    assert call["max_tokens"] >= 4000
