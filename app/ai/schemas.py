"""Pydantic-схемы структурированного вывода Claude для app.ai."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TicketCategory = Literal["cheat", "bug", "loot_loss", "dispute", "ban_appeal", "other"]


class TicketExtraction(BaseModel):
    """Результат extract_ticket: поля, извлечённые из текста тикета игрока."""

    player_name: str | None = Field(default=None, description="Ник игрока, если указан в тикете")
    player_id: str | None = Field(default=None, description="ID/Steam ID игрока, если указан в тикете")
    category: TicketCategory = Field(description="Тип обращения")
    summary: str = Field(description="Короткая суть жалобы своими словами")
    time_hint: str | None = Field(
        default=None, description="Время/период инцидента, как упомянуто в тикете (может быть неточным)"
    )
    needs_logs: bool = Field(description="Нужно ли поднимать логи сервера, чтобы разобраться в тикете")


class AdviceResponse(BaseModel):
    """Результат advise: структурированный разбор тикета для админа."""

    summary: str = Field(description="Короткая суть тикета")
    facts: list[str] = Field(description="Факты, подтверждённые логами и кейсами; пусто, если фактов нет")
    similar: list[str] = Field(description="Ссылки/описания похожих кейсов, которые реально релевантны")
    recommendation: str = Field(description="Рекомендация админу по решению тикета")
    confidence: float = Field(ge=0.0, le=1.0, description="Уверенность в рекомендации от 0 до 1")
    open_questions: list[str] = Field(
        description="Вопросы/недостающие данные, которые мешают дать более уверенный ответ"
    )
