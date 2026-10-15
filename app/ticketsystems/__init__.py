from .base import InboundTicket, TicketSystem, WebhookError
from .zammad import ZammadSystem
from .zendesk import ZendeskSystem


def build_systems() -> dict[str, TicketSystem]:
    """One adapter per supported system, configured from the environment."""
    return {s.name: s for s in (ZendeskSystem.from_env(), ZammadSystem.from_env())}


__all__ = ["InboundTicket", "TicketSystem", "WebhookError", "ZammadSystem", "ZendeskSystem", "build_systems"]
