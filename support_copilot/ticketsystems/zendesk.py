"""Zendesk adapter (concept): Support API + signed webhooks."""
import base64
import hashlib
import hmac
import os
from collections.abc import Mapping

from pydantic import BaseModel, ValidationError

from ..models import Category, Priority
from .base import InboundTicket, TicketSystem, WebhookError, split_tags

PRIORITY_MAP = {Priority.P1: "urgent", Priority.P2: "high", Priority.P3: "normal", Priority.P4: "low"}
TYPE_MAP = {  # Zendesk types: problem | incident | question | task
    Category.BUG: "incident", Category.BILLING: "question", Category.ACCESS: "question",
    Category.HOWTO: "question", Category.FEATURE_REQUEST: "task", Category.OTHER: "question",
}


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Zendesk webhook signature: base64(HMAC-SHA256(secret, timestamp + body))."""
    digest = hmac.new(secret.encode(), timestamp.encode() + body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")


class _Payload(BaseModel):
    ticket_id: int
    subject: str
    description: str
    requester: str = "unknown"
    tags: list[str] | str | None = None  # template: "{{ticket.tags}}" (renders as a space separated string)
    agent: str | None = None  # template: "{{current_user.email}}"


class ZendeskSystem(TicketSystem):
    name = "zendesk"
    secret_env = "ZENDESK_WEBHOOK_SECRET"

    def __init__(self, subdomain=None, email=None, api_token=None, transport=None):
        self.subdomain, self.email, self.api_token = subdomain, email, api_token
        self.transport = transport

    @classmethod
    def from_env(cls):
        return cls(os.getenv("ZENDESK_SUBDOMAIN"), os.getenv("ZENDESK_EMAIL"), os.getenv("ZENDESK_API_TOKEN"))

    @property
    def configured(self) -> bool:
        return bool(self.subdomain and self.email and self.api_token)

    @property
    def base_url(self) -> str:
        return f"https://{self.subdomain}.zendesk.com"

    def basic_auth(self):
        return (f"{self.email}/token", self.api_token)

    def verify_webhook(self, headers: Mapping[str, str], raw: bytes) -> bool:
        secret = os.getenv(self.secret_env)
        if not secret:
            return True
        return verify_signature(secret, headers.get("X-Zendesk-Webhook-Signature-Timestamp", ""), raw,
                                headers.get("X-Zendesk-Webhook-Signature", ""))

    def parse_webhook(self, raw: bytes) -> InboundTicket:
        try:
            p = _Payload.model_validate_json(raw)
        except ValidationError as e:
            raise WebhookError(str(e)) from e
        return InboundTicket(str(p.ticket_id), p.subject, p.description, p.requester, split_tags(p.tags),
                             p.agent or None)

    def _update(self, ext_id, *, comment, public, priority=None, category=None, status=None, tags=None):
        ticket: dict = {"comment": {"body": comment, "public": public}}
        if priority:
            ticket["priority"] = PRIORITY_MAP[priority]
        if category:
            ticket["type"] = TYPE_MAP[category]
        if status:
            ticket["status"] = status
        if tags:
            ticket["additional_tags"] = tags  # additive; "tags" would REPLACE all existing tags
        return self._execute([{"method": "PUT", "path": f"/api/v2/tickets/{ext_id}.json",
                               "json": {"ticket": ticket}}])

    def post_internal_note(self, external_id, note, *, priority, category, tags):
        return self._update(external_id, comment=note, public=False, priority=priority,
                            category=category, tags=tags)

    def post_public_reply(self, external_id, reply, *, priority, category):
        return self._update(external_id, comment=reply, public=True, priority=priority,
                            category=category, status="pending")

    def post_rejection(self, external_id, reviewer, *, tag="ai_rejected"):
        return self._update(external_id, public=False, tags=[tag],
                            comment=f"[AI triage] Suggestion rejected by {reviewer}. Handle manually.")
