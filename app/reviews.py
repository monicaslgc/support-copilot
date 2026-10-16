"""A human decision on a ticket."""
import json

from fastapi import HTTPException

from . import db, guardrails, ingest, state
from .models import Category, Decision, Priority, Review, Status


def apply_review(ticket_id: int, r: Review) -> dict:
    """Record a human decision. For tickets from a ticket system, an approved reply goes to the customer
    as a public reply."""
    row = state.fetch_one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    if not row:
        raise HTTPException(404, "ticket not found")
    s = json.loads(row["suggestion"])
    ai = s["triage"]
    final_cat = r.final_category.value if r.final_category else ai["category"]
    final_pri = r.final_priority.value if r.final_priority else ai["priority"]
    final_reply = r.edited_reply or s["draft_reply"]
    with state.lock:
        # Atomic claim: of two simultaneous reviews exactly one changes the row, the other gets a 409.
        claimed = state.conn.execute("UPDATE tickets SET status = ? WHERE id = ? AND status = ?",
                                     (r.action.value, ticket_id, Status.PENDING_REVIEW.value)).rowcount
        if not claimed:
            state.conn.rollback()
            raise HTTPException(409, "ticket already reviewed")
        state.conn.execute(
            "INSERT INTO reviews (ticket_id, created_at, reviewer, action, final_category, final_priority,"
            " final_reply, ai_category, ai_priority, ai_reply, reasons, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ticket_id, db.now(), r.reviewer, r.action.value, final_cat, final_pri, final_reply,
             ai["category"], ai["priority"], s["draft_reply"], json.dumps([x.value for x in r.reasons]), r.note),
        )
        state.conn.commit()

    system = state.systems.get(row["system"]) if row["system"] else None
    sync = None
    if system:
        ext = row["external_id"]
        if r.action is Decision.APPROVED:
            sync = ingest.push(system, ext, "public_reply", system.post_public_reply, final_reply,
                               priority=Priority(final_pri), category=Category(final_cat))
        else:
            sync = ingest.push(system, ext, "rejection_note", system.post_rejection, r.reviewer)
    return {"id": ticket_id, "status": r.action, "sync": sync, "warnings": guardrails.find_commitments(final_reply)}
