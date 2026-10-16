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

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .copilot import Copilot, CopilotError
from .models import Review, Status, TicketIn


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
    def get_ticket(ticket_id: int):
        return cp.get_ticket(ticket_id)

    @api.post("/tickets/{ticket_id}/review", tags=["tickets"])
    @_http
    def review_ticket(ticket_id: int, r: Review):
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
    def review_cluster(cluster_id: int, r: Review):
        """One decision for every pending ticket of a possible incident."""
        return cp.review_cluster(cluster_id, r)

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

    @router.post("/webhooks/{system_name}", status_code=201, tags=["webhooks"])
    async def ticket_system_webhook(system_name: str, request: Request):
        """Inbound webhook from a ticket system (zendesk, zammad). Configure it on 'ticket created'."""
        raw = await request.body()
        try:
            # The model and ticket-system calls block for seconds: keep them off the event loop.
            return await run_in_threadpool(cp.handle_webhook, system_name, request.headers, raw)
        except CopilotError as e:
            raise HTTPException(e.status, e.detail) from e

    router.include_router(api)
    return router
