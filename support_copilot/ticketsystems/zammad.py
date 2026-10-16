"""Zammad adapter: REST API (token auth) + webhooks signed with HMAC-SHA1.

Zammad is open source and self-hostable, so this is the adapter that can be tested
against a real instance without a paid account.

Defaults assumed: priorities 1 low / 2 normal / 3 high (Zammad has no "urgent" out of
the box, so P1 and P2 both map to high; the P1/P2 distinction is kept in the internal note).
"""
import hashlib
import hmac
import json
import os
from collections.abc import Mapping

from ..models import Priority
from .base import InboundTicket, TicketSystem, WebhookError, split_tags

PRIORITY_ID = {Priority.P1: 3, Priority.P2: 3, Priority.P3: 2, Priority.P4: 1}


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """Zammad sends `X-Hub-Signature: sha1=<hex HMAC-SHA1 of the body>`."""
    expected = "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
    return hmac.compare_digest(expected, signature or "")


class ZammadSystem(TicketSystem):
    name = "zammad"
    secret_env = "ZAMMAD_WEBHOOK_SECRET"

    def __init__(self, url=None, token=None, transport=None):
        self.url, self.token = (url or "").rstrip("/") or None, token
        self.transport = transport

    @classmethod
    def from_env(cls):
        return cls(os.getenv("ZAMMAD_URL"), os.getenv("ZAMMAD_TOKEN"))

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)

    @property
    def base_url(self) -> str:
        return self.url or ""

    def auth_headers(self):
        return {"Authorization": f"Token token={self.token}"}

    def verify_webhook(self, headers: Mapping[str, str], raw: bytes) -> bool:
        secret = os.getenv(self.secret_env)
        if not secret:
            return True
        return verify_signature(secret, raw, headers.get("X-Hub-Signature", ""))

    def parse_webhook(self, raw: bytes) -> InboundTicket:
        """Parses Zammad's default webhook payload: {"ticket": {...}, "article": {...}}."""
        try:
            d = json.loads(raw)
            ticket, article = d["ticket"], d.get("article") or {}
            ext_id, subject = ticket["id"], ticket["title"]
        except (ValueError, KeyError, TypeError) as e:
            raise WebhookError(f"not a Zammad ticket payload: {e!r}") from e
        body = article.get("body") or ticket.get("title")
        who = article.get("from") or ticket.get("customer") or "unknown"
        by = ticket.get("updated_by")
        actor = (by.get("email") or by.get("login")) if isinstance(by, dict) else by
        return InboundTicket(str(ext_id), str(subject), str(body), str(who), split_tags(ticket.get("tags")),
                             str(actor) if actor else None)

    probe_path = "/api/v1/users/me"

    def read_probe(self, response):
        user = response.json()
        return True, f"authenticated as {user.get('email') or user.get('login')}"

    @staticmethod
    def _article(ext_id, body, internal):
        return {"method": "POST", "path": "/api/v1/ticket_articles", "json": {
            "ticket_id": int(ext_id), "subject": "AI triage" if internal else "Re: your request",
            "body": body, "type": "note", "internal": internal, "content_type": "text/plain"}}

    @staticmethod
    def _priority(ext_id, priority):
        return {"method": "PUT", "path": f"/api/v1/tickets/{int(ext_id)}",
                "json": {"priority_id": PRIORITY_ID[priority]}}

    @staticmethod
    def _tag(ext_id, tag):  # tags/add is additive: it never removes the agent's tags
        return {"method": "POST", "path": "/api/v1/tags/add",
                "json": {"object": "Ticket", "o_id": int(ext_id), "item": tag}}

    def post_internal_note(self, external_id, note, *, priority, category, tags):
        return self._execute([self._priority(external_id, priority),
                              self._article(external_id, note, internal=True),
                              *[self._tag(external_id, t) for t in tags]])

    def post_public_reply(self, external_id, reply, *, priority, category):
        return self._execute([self._priority(external_id, priority),
                              self._article(external_id, reply, internal=False)])

    def post_rejection(self, external_id, reviewer, *, tag="ai_rejected"):
        return self._execute([
            self._article(external_id, f"[AI triage] Suggestion rejected by {reviewer}. Handle manually.",
                          internal=True),
            self._tag(external_id, tag)])
