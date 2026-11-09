"""app.ai — обёртка над Claude API (SDK anthropic): extract_ticket, advise."""
from app.ai.client import get_client, set_client
from app.ai.core import advise, extract_ticket
from app.ai.exceptions import ClaudeRefusalError
from app.ai.schemas import AdviceResponse, TicketExtraction

__all__ = [
    "extract_ticket",
    "advise",
    "TicketExtraction",
    "AdviceResponse",
    "ClaudeRefusalError",
    "get_client",
    "set_client",
]
