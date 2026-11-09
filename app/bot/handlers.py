"""Бизнес-логика бота, отделённая от discord-объектов.

Все функции здесь принимают/возвращают простые dataclass'ы (свои или из
app.cases) и работают через app.cases/app.ai/app.logs. Ничего не знает про
discord.Message/discord.Embed — это позволяет тестировать pipeline без сети
и без discord gateway.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from anthropic import Anthropic

from app.ai import advise, extract_ticket
from app.ai.schemas import AdviceResponse, TicketExtraction
from app.cases import Case, CaseStore, Decision
from app.logs import LogSource, NullLogSource

SimilarCase = tuple[Case, Optional[Decision], float, str]


@dataclass
class Ticket:
    """Тикет игрока — то, что нужно для разбора, без привязки к discord.Message."""

    text: str
    source_channel_id: Optional[int] = None
    source_message_id: Optional[int] = None
    player_name: Optional[str] = None
    player_id: Optional[int] = None


@dataclass
class AnalysisResult:
    """Результат полного разбора тикета: кейс + извлечение + похожие + совет."""

    case: Case
    extraction: TicketExtraction
    similar: list[SimilarCase]
    advice: AdviceResponse


@dataclass
class CaseDetail:
    """Карточка кейса: сам кейс + решение, если оно уже принято."""

    case: Case
    decision: Optional[Decision]


def analyze_ticket(
    store: CaseStore,
    ticket: Ticket,
    *,
    log_source: Optional[LogSource] = None,
    ai_client: Optional[Anthropic] = None,
    k_similar: int = 5,
) -> AnalysisResult:
    """Полный pipeline нового тикета: add_case -> extract_ticket -> find_similar -> advise."""
    log_source = log_source or NullLogSource()

    case = store.add_case(
        ticket.text,
        source_channel_id=ticket.source_channel_id,
        source_message_id=ticket.source_message_id,
        player_name=ticket.player_name,
        player_id=ticket.player_id,
    )

    extraction = extract_ticket(ticket.text, client=ai_client)

    similar = [
        item for item in store.find_similar(ticket.text, k=k_similar) if item[0].id != case.id
    ]

    log_facts = log_source.query(player=extraction.player_name or ticket.player_name)

    advice = advise(extraction, similar, log_facts, client=ai_client)

    return AnalysisResult(case=case, extraction=extraction, similar=similar, advice=advice)


def search_similar(store: CaseStore, text: str, k: int = 5) -> list[SimilarCase]:
    """Поиск похожих кейсов по тексту, без обращения к Claude."""
    return store.find_similar(text, k=k)


def resolve_ticket_case(
    store: CaseStore, case_id: int, admin_id: int, decision_text: str
) -> Decision:
    """Фиксирует решение админа по кейсу (эмбеддинг кейса пересчитывается внутри app.cases)."""
    return store.resolve_case(case_id, admin_id, decision_text)


def get_case_detail(store: CaseStore, case_id: int) -> Optional[CaseDetail]:
    """Карточка кейса: данные + решение, если есть. None, если кейса не существует."""
    case = store.get_case(case_id)
    if case is None:
        return None
    return CaseDetail(case=case, decision=store.get_decision(case_id))
