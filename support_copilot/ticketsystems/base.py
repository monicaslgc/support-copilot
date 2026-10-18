"""Common interface every ticket system adapter implements.

The core (triage, review queue, metrics) only talks to `TicketSystem`; adding a new
system means writing one adapter with these methods, nothing else changes.
"""
import re
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
    tags: tuple[str, ...] = ()  # the ticket's tags at the time of the webhook (how a person decides from the helpdesk)
    actor: str | None = None  # who triggered the webhook (an agent's email or name), when the payload says


def split_tags(value) -> tuple[str, ...]:
    """Tags as a list, or as the space/comma separated string a helpdesk's webhook template renders."""
    if not value or not isinstance(value, (str, list, tuple)):  # anything else is not a list of tags
        return ()
    items = re.split(r"[\s,]+", value) if isinstance(value, str) else [str(v) for v in value]
    return tuple(t.strip().lower() for t in items if t and t.strip())


class WebhookError(ValueError):
    """The inbound webhook body is not a usable ticket."""


class TicketSystem(ABC):
    name: str
    secret_env: str = ""  # environment variable holding this system's webhook secret
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
    def post_rejection(self, external_id: str, reviewer: str, *, tag: str = "ai_rejected") -> dict:
        """Internal note only: the AI suggestion was rejected."""

    # --- a read-only call that proves the credentials work ---
    probe_path: str = ""  # a GET that changes nothing, e.g. "who am I"

    def read_probe(self, response: httpx.Response) -> tuple[bool, str]:
        """(ok, what to tell the person) from the answer to `probe_path`."""
        return True, "the credentials work"

    def check_connection(self) -> tuple[bool, str]:
        """Call `probe_path` and say whether the credentials work. Used by `python -m support_copilot.doctor --live`."""
        if not self.configured:
            return False, "not configured: the credentials are missing"
        try:
            with httpx.Client(transport=self.transport, timeout=10, base_url=self.base_url,
                              headers=self.auth_headers(), auth=self.basic_auth()) as http:
                r = http.get(self.probe_path)
        except httpx.HTTPError as e:
            return False, f"could not reach {self.base_url}: {type(e).__name__}: {e}"
        if r.status_code in (401, 403):
            return False, f"{r.status_code}: the credentials were refused"
        if r.status_code >= 400:
            return False, f"{r.status_code} from {self.probe_path}"
        return self.read_probe(r)

    # --- shared executor ---
    def _execute(self, requests: list[dict]) -> dict:
        """Send requests in order, or return them untouched in dry-run (no credentials)."""
        if not self.configured:
            return {"mode": "dry_run", "requests": requests}
        try:
            with httpx.Client(transport=self.transport, timeout=10, base_url=self.base_url,
                              headers=self.auth_headers(), auth=self.basic_auth()) as http:
                for r in requests:
                    http.request(r["method"], r["path"], json=r["json"]).raise_for_status()
        except Exception as e:
            e.requests = requests  # type: ignore[attr-defined]  # kept in the outbox so a failed send can be retried
            raise
        return {"mode": "sent", "requests": requests}

    def resend(self, requests: list[dict]) -> dict:
        """Send requests again, as recorded in the outbox."""
        return self._execute(requests)
