import json
import os

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ValidationError

from .classifier import classify
from .db import connect, now, row_to_ticket
from .drafting import draft_reply
from .kb import KnowledgeBase
from .models import Category, Priority, Review, Status, Suggestion, TicketIn
from .zendesk import PRIORITY_MAP, TYPE_MAP, ZendeskClient, verify_signature

app = FastAPI(title="Ticket Triage (human-in-the-loop, Zendesk-ready)")
kb = KnowledgeBase()
conn = connect()
zendesk = ZendeskClient.from_env()


def _suggest(t: TicketIn) -> Suggestion:
    triage = classify(t.subject, t.body)
    articles = kb.search(f"{t.subject} {t.body}")
    return Suggestion(
        triage=triage,
        articles=[{k: a[k] for k in ("id", "title", "score")} for a in articles],
        draft_reply=draft_reply(t.subject, t.body, triage, articles, t.customer),
    )


def _store(t: TicketIn, s: Suggestion, zendesk_ticket_id: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO tickets (created_at, subject, body, customer, suggestion, zendesk_ticket_id)"
        " VALUES (?,?,?,?,?,?)",
        (now(), t.subject, t.body, t.customer, s.model_dump_json(), zendesk_ticket_id),
    )
    conn.commit()
    return cur.lastrowid


def _push(zd_id: int, kind: str, **kwargs):
    """Call Zendesk and log it. A Zendesk outage must never lose our triage or the human's decision."""
    try:
        res = zendesk.update_ticket(zd_id, **kwargs)
        mode, payload, err = res["mode"], res["payload"], None
    except Exception as e:
        mode, payload, err = "error", {"kwargs": kwargs}, str(e)
    conn.execute(
        "INSERT INTO outbox (created_at, zendesk_ticket_id, kind, mode, payload, error) VALUES (?,?,?,?,?,?)",
        (now(), zd_id, kind, mode, json.dumps(payload), err),
    )
    conn.commit()
    return mode


def _internal_note(s: Suggestion) -> str:
    tr = s.triage
    arts = ", ".join(a["title"] for a in s.articles) or "none"
    return (
        f"[AI triage - pending human review]\n"
        f"Category: {tr.category.value} | Priority: {tr.priority.value} | Team: {tr.team} "
        f"| Confidence: {tr.confidence} ({tr.source})\n"
        f"Why: {tr.rationale}\nKB articles: {arts}\n\nSuggested reply:\n{s.draft_reply}"
    )


@app.post("/tickets", status_code=201)
def create_ticket(t: TicketIn):
    """Triage a ticket with AI. Nothing is sent: it waits for human review."""
    s = _suggest(t)
    return {"id": _store(t, s), "status": Status.PENDING_REVIEW, "suggestion": s}


class ZendeskWebhook(BaseModel):
    ticket_id: int
    subject: str
    description: str
    requester: str = "unknown"


@app.post("/webhooks/zendesk", status_code=201)
async def zendesk_webhook(request: Request):
    """Zendesk webhook target (fire it from a trigger on 'ticket created').

    Body template (Admin Center > Webhooks):
      {"ticket_id":"{{ticket.id}}","subject":"{{ticket.title}}",
       "description":"{{ticket.description}}","requester":"{{ticket.requester.name}}"}
    """
    raw = await request.body()
    secret = os.getenv("ZENDESK_WEBHOOK_SECRET")
    if secret and not verify_signature(
        secret,
        request.headers.get("X-Zendesk-Webhook-Signature-Timestamp", ""),
        raw,
        request.headers.get("X-Zendesk-Webhook-Signature", ""),
    ):
        raise HTTPException(401, "invalid webhook signature")
    try:
        w = ZendeskWebhook.model_validate_json(raw)
        t = TicketIn(subject=w.subject, body=w.description, customer=w.requester)
    except ValidationError as e:
        raise HTTPException(422, json.loads(e.json()))

    # Zendesk retries failed deliveries: make this idempotent per Zendesk ticket.
    row = conn.execute("SELECT id FROM tickets WHERE zendesk_ticket_id = ?", (w.ticket_id,)).fetchone()
    if row:
        return {"id": row["id"], "duplicate": True}

    s = _suggest(t)
    tid = _store(t, s, w.ticket_id)
    # Internal note only (public=False): the customer sees nothing until a human approves.
    _push(
        w.ticket_id, "triage_note",
        comment=_internal_note(s), public=False,
        priority=PRIORITY_MAP[s.triage.priority], ticket_type=TYPE_MAP[s.triage.category],
        tags=["ai_triaged", f"ai_cat_{s.triage.category.value}", f"ai_team_{s.triage.team}"],
    )
    return {"id": tid, "status": Status.PENDING_REVIEW, "suggestion": s}


@app.get("/tickets")
def list_tickets(status: Status | None = None):
    """Review queue, most urgent first."""
    q = "SELECT * FROM tickets" + (" WHERE status = ?" if status else "")
    rows = conn.execute(q, (status.value,) if status else ()).fetchall()
    tickets = [row_to_ticket(r) for r in rows]
    tickets.sort(key=lambda t: (t["suggestion"]["triage"]["priority"], t["created_at"]))
    return tickets


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not row:
        raise HTTPException(404, "ticket not found")
    return row_to_ticket(row)


@app.post("/tickets/{ticket_id}/review")
def review_ticket(ticket_id: int, r: Review):
    """Human decision. Approved replies go to the customer (Zendesk tickets) as a public comment."""
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not row:
        raise HTTPException(404, "ticket not found")
    if row["status"] != Status.PENDING_REVIEW.value:
        raise HTTPException(409, "ticket already reviewed")
    if r.action is Status.PENDING_REVIEW:
        raise HTTPException(422, "action must be approved or rejected")
    s = json.loads(row["suggestion"])
    ai = s["triage"]
    final_cat = r.final_category.value if r.final_category else ai["category"]
    final_pri = r.final_priority.value if r.final_priority else ai["priority"]
    final_reply = r.edited_reply or s["draft_reply"]
    conn.execute(
        "INSERT INTO reviews (ticket_id, created_at, reviewer, action, final_category, final_priority,"
        " final_reply, ai_category, ai_priority) VALUES (?,?,?,?,?,?,?,?,?)",
        (ticket_id, now(), r.reviewer, r.action.value, final_cat, final_pri, final_reply,
         ai["category"], ai["priority"]),
    )
    conn.execute("UPDATE tickets SET status = ? WHERE id = ?", (r.action.value, ticket_id))
    conn.commit()

    zd_id = row["zendesk_ticket_id"]
    sync = None
    if zd_id:
        if r.action is Status.APPROVED:
            sync = _push(
                zd_id, "public_reply", comment=final_reply, public=True, status="pending",
                priority=PRIORITY_MAP[Priority(final_pri)], ticket_type=TYPE_MAP[Category(final_cat)],
            )
        else:
            sync = _push(
                zd_id, "rejection_note", public=False,
                comment=f"[AI triage] Suggestion rejected by {r.reviewer}. Handle manually.",
                tags=["ai_rejected"],
            )
    return {"id": ticket_id, "status": r.action, "zendesk_sync": sync}


@app.get("/metrics")
def metrics():
    """How often humans agree with the AI: the number that decides whether to automate more."""
    rows = conn.execute("SELECT * FROM reviews WHERE action = 'approved'").fetchall()
    n = len(rows)
    if not n:
        return {"reviewed": 0}
    cat_ok = sum(r["ai_category"] == r["final_category"] for r in rows)
    pri_ok = sum(r["ai_priority"] == r["final_priority"] for r in rows)
    edited = sum(
        json.loads(conn.execute("SELECT suggestion FROM tickets WHERE id=?", (r["ticket_id"],)).fetchone()[0])["draft_reply"]
        != r["final_reply"]
        for r in rows
    )
    return {
        "reviewed": n,
        "category_agreement": round(cat_ok / n, 2),
        "priority_agreement": round(pri_ok / n, 2),
        "reply_edit_rate": round(edited / n, 2),
    }


@app.get("/outbox")
def outbox(limit: int = 50):
    """Audit log of calls to Zendesk (sent / dry_run / error)."""
    rows = conn.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]
