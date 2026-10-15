"""Common interface every ticket system adapter implements.

The core (triage, review queue, metrics) only talks to `TicketSystem`; adding a new
system means writing one adapter with these methods, nothing else changes.
"""
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from ..models import Category, Priority


@dataclass(frozen=True)
class InboundTicket:
    external_id: str
    subject: str
    body: str
    requester: str = "unknown"


class WebhookError(ValueError):
    """The inbound webhook body is not a usable ticket."""


class TicketSystem(ABC):
    name: str
    transport: httpx.BaseTransport | None = None  # injectable in tests

    # --- connection (used by the shared HTTP executor) ---
    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @property
    @abstractmethod
    def base_url(self) -> str: ...

    def auth_headers(self) -> dict[str, str]:
        return {}

    def basic_auth(self) -> tuple[str, str] | None:
        return None

    # --- inbound ---
    @abstractmethod
    def verify_webhook(self, headers: Mapping[str, str], raw: bytes) -> bool:
        """True if the webhook is authentic (or no secret is configured)."""

    @abstractmethod
    def parse_webhook(self, raw: bytes) -> InboundTicket:
        """Raise WebhookError if the body is not a usable ticket."""

    # --- outbound: each returns {"mode": "sent"|"dry_run", "requests": [...]} ---
    @abstractmethod
    def post_internal_note(self, external_id: str, note: str, *, priority: Priority,
                           category: Category, tags: list[str]) -> dict:
        """Internal note + priority/category/tags. Must NEVER be visible to the customer."""

    @abstractmethod
    def post_public_reply(self, external_id: str, reply: str, *, priority: Priority,
                          category: Category) -> dict:
        """The human-approved reply, visible to the customer."""

    @abstractmethod
    def post_rejection(self, external_id: str, reviewer: str) -> dict:
        """Internal note only: the AI suggestion was rejected."""

    # --- shared executor ---
    def _execute(self, requests: list[dict]) -> dict:
        """Send requests in order, or return them untouched in dry-run (no credentials)."""
        if not self.configured:
            return {"mode": "dry_run", "requests": requests}
        with httpx.Client(transport=self.transport, timeout=10, base_url=self.base_url,
                          headers=self.auth_headers(), auth=self.basic_auth()) as http:
            for r in requests:
                http.request(r["method"], r["path"], json=r["json"]).raise_for_status()
        return {"mode": "sent", "requests": requests}
