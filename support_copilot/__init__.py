"""support-copilot: human-in-the-loop AI triage for support tickets, as a library you embed in your own stack.

    from support_copilot import Copilot, TicketIn
    cp = Copilot(kb="our-articles/")
    suggestion = cp.suggest(TicketIn(subject="Refund please", body="Charged twice", customer="Ana"))

See docs/embedding.md.
"""
from .config import Taxonomy
from .copilot import Copilot, CopilotError
from .kb import KnowledgeBase
from .models import Decision, Review, Status, Suggestion, TicketIn
from .ticketsystems import InboundTicket, TicketSystem, WebhookError

__version__ = "0.1.0"
__all__ = ["Copilot", "CopilotError", "Decision", "InboundTicket", "KnowledgeBase", "Review", "Status",
           "Suggestion", "Taxonomy", "TicketIn", "TicketSystem", "WebhookError"]
