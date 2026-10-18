"""The HTTP face of a Copilot, as a FastAPI router you can mount in your own app.

    from fastapi import FastAPI
    from support_copilot import Copilot
    from support_copilot.api import create_router

    app = FastAPI()
    app.include_router(create_router(Copilot(db="tickets.db", kb="our-articles/")), prefix="/copilot")

Review and ops endpoints require `X-API-Key` when the Copilot has an api_key (or API_KEY is set). Webhooks
use each ticket system's own signature instead. Add your own authentication around the router as needed.
"""
from functools import wraps
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .copilot import MAX_WEBHOOK_BYTES, Copilot, CopilotError
from .models import ClusterReview, Review, Status, TicketIn

# an id larger than SQLite's integers would be a 500 from the database; it is simply not a ticket
TicketId = Annotated[int, Path(ge=1, le=2**63 - 1)]


class ReplyCheck(BaseModel):
    text: str = Field(max_length=10_000)


def _http(fn):
    """Answer a refused request with its HTTP status instead of a 500."""
    @wraps(fn)
    def inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except CopilotError as e:
            raise HTTPException(e.status, e.detail) from e
    return inner


def create_router(cp: Copilot) -> APIRouter:
    def require_api_key(x_api_key: str | None = Header(default=None)):
        if not cp.check_api_key(x_api_key):
            raise HTTPException(401, "missing or invalid X-API-Key")

    router = APIRouter()
    api = APIRouter(dependencies=[Depends(require_api_key)])

    @api.post("/tickets", status_code=201, tags=["tickets"])
    def create_ticket(t: TicketIn):
        """Triage a ticket with AI. Nothing is sent: it waits for human review."""
        return cp.submit(t)

    @api.get("/tickets", tags=["tickets"])
    def list_tickets(status: Status | None = None, limit: int = Query(100, ge=1, le=500)):
        """Review queue, most urgent first."""
        return cp.list_tickets(status, limit)

    @api.get("/tickets/{ticket_id}", tags=["tickets"])
    @_http
    def get_ticket(ticket_id: TicketId):
        return cp.get_ticket(ticket_id)

    @api.post("/tickets/{ticket_id}/review", tags=["tickets"])
    @_http
    def review_ticket(ticket_id: TicketId, r: Review):
        return cp.review(ticket_id, r)

    @api.post("/check-reply", tags=["tickets"])
    def check_reply(body: ReplyCheck):
        """Promises in a reply a person is about to send (refunds, credits, guarantees, amounts, deadlines)."""
        from . import guardrails
        flags = guardrails.find_commitments(body.text)
        return {"warnings": flags, "explanation": guardrails.explain(flags)}

    @api.get("/clusters", tags=["incidents"])
    def list_clusters():
        """Groups of tickets about the same issue that still have something to review: possible incidents first."""
        return cp.list_clusters()

    @api.post("/clusters/{cluster_id}/review", tags=["incidents"])
    @_http
    def review_cluster(cluster_id: TicketId, r: ClusterReview):
        """One decision for the tickets of a possible incident that the reviewer was shown (`ticket_ids`) and that
        are still pending. Tickets that joined later stay pending and are listed in `left_pending`."""
        return cp.review_cluster(cluster_id, r, r.ticket_ids)

    @api.get("/metrics", tags=["metrics"])
    def metrics():
        """How often humans agree with the AI: the number that decides whether to automate more."""
        return cp.metrics()

    @api.get("/metrics/breakdown", tags=["metrics"])
    def metrics_breakdown():
        """Quality of the AI per category, why humans corrected it, and where it may have earned more autonomy."""
        return cp.metrics_breakdown()

    @api.get("/outbox", tags=["metrics"])
    def outbox(limit: int = Query(50, ge=1, le=500)):
        """Audit log of calls to ticket systems (sent / dry_run / error)."""
        return cp.outbox(limit)

    @api.post("/outbox/{outbox_id}/retry", tags=["metrics"])
    @_http
    def retry_outbox(outbox_id: TicketId):
        """Send a failed ticket-system call again. `409` if it did not fail or a retry already sent it."""
        return cp.retry_outbox(outbox_id)

    @router.post("/webhooks/{system_name}", status_code=201, tags=["webhooks"])
    async def ticket_system_webhook(system_name: str, request: Request):
        """Inbound webhook from a ticket system (zendesk, zammad). Configure it on 'ticket created'."""
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_WEBHOOK_BYTES:  # refuse before reading it into memory
            raise HTTPException(413, "webhook body too large")
        raw = b""
        async for chunk in request.stream():  # a running cap: a body sent in chunks has no Content-Length to check
            raw += chunk
            if len(raw) > MAX_WEBHOOK_BYTES:
                raise HTTPException(413, "webhook body too large")
        try:
            # The model and ticket-system calls block for seconds: keep them off the event loop.
            return await run_in_threadpool(cp.handle_webhook, system_name, request.headers, raw)
        except CopilotError as e:
            raise HTTPException(e.status, e.detail) from e

    router.include_router(api)
    return router
