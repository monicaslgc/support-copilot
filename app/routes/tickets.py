from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import guardrails, ingest, queries, reviews, state
from ..db import row_to_ticket
from ..models import Review, Status, TicketIn
from ..security import require_api_key

router = APIRouter(tags=["tickets"], dependencies=[Depends(require_api_key)])


@router.post("/tickets", status_code=201)
def create_ticket(t: TicketIn):
    """Triage a ticket with AI. Nothing is sent: it waits for human review."""
    s = ingest.suggest(t)
    return {"id": ingest.store(t, s), "status": Status.PENDING_REVIEW, "suggestion": s}


@router.get("/tickets")
def list_tickets(status: Status | None = None, limit: int = Query(100, ge=1, le=500)):
    """Review queue, most urgent first."""
    where, params = ("WHERE status = ? ", (status.value,)) if status else ("", ())
    rows = state.fetch_all(f"SELECT * FROM tickets {where}ORDER BY priority, created_at, id LIMIT ?", (*params, limit))
    return queries.enrich([row_to_ticket(r) for r in rows])


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    row = state.fetch_one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    if not row:
        raise HTTPException(404, "ticket not found")
    return queries.enrich([row_to_ticket(row)])[0]


@router.post("/tickets/{ticket_id}/review")
def review_ticket(ticket_id: int, r: Review):
    return reviews.apply_review(ticket_id, r)


class ReplyCheck(BaseModel):
    text: str = Field(max_length=10_000)


@router.post("/check-reply")
def check_reply(body: ReplyCheck):
    """Promises in a reply a person is about to send (refunds, credits, guarantees, amounts, deadlines)."""
    flags = guardrails.find_commitments(body.text)
    return {"warnings": flags, "explanation": guardrails.explain(flags)}
