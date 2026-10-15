"""Zendesk adapter: inbound webhook verification + outbound ticket updates.

Outbound calls use the Zendesk Support API (PUT /api/v2/tickets/{id}.json).
Without credentials the client runs in dry-run mode: it builds the exact
payload but sends nothing, so the flow can be developed and demoed offline.
"""
import base64
import hashlib
import hmac
import os

import httpx

from .models import Category, Priority

# Zendesk priorities: urgent | high | normal | low
PRIORITY_MAP = {Priority.P1: "urgent", Priority.P2: "high", Priority.P3: "normal", Priority.P4: "low"}
# Zendesk types: problem | incident | question | task
TYPE_MAP = {
    Category.BUG: "incident",
    Category.BILLING: "question",
    Category.ACCESS: "question",
    Category.HOWTO: "question",
    Category.FEATURE_REQUEST: "task",
    Category.OTHER: "question",
}


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Zendesk webhook signature: base64(HMAC-SHA256(secret, timestamp + body))."""
    digest = hmac.new(secret.encode(), timestamp.encode() + body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")


class ZendeskClient:
    def __init__(self, subdomain=None, email=None, api_token=None, transport=None):
        self.subdomain, self.email, self.api_token = subdomain, email, api_token
        self.transport = transport  # injectable for tests

    @classmethod
    def from_env(cls):
        return cls(
            os.getenv("ZENDESK_SUBDOMAIN"), os.getenv("ZENDESK_EMAIL"), os.getenv("ZENDESK_API_TOKEN")
        )

    @property
    def configured(self) -> bool:
        return bool(self.subdomain and self.email and self.api_token)

    def update_ticket(self, ticket_id: int, *, comment: str, public: bool, priority=None,
                      ticket_type=None, status=None, tags=None) -> dict:
        """Returns {"mode": "sent"|"dry_run", "payload": ...}. Raises on HTTP errors."""
        ticket: dict = {"comment": {"body": comment, "public": public}}
        if priority:
            ticket["priority"] = priority
        if ticket_type:
            ticket["type"] = ticket_type
        if status:
            ticket["status"] = status
        if tags:
            ticket["additional_tags"] = tags  # additive; "tags" would REPLACE all existing tags
        payload = {"ticket": ticket}
        if not self.configured:
            return {"mode": "dry_run", "payload": payload}
        with httpx.Client(transport=self.transport, timeout=10) as http:
            r = http.put(
                f"https://{self.subdomain}.zendesk.com/api/v2/tickets/{ticket_id}.json",
                json=payload,
                auth=(f"{self.email}/token", self.api_token),
            )
            r.raise_for_status()
        return {"mode": "sent", "payload": payload}
