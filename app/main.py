import json

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from .classifier import classify
from .db import connect, now, row_to_ticket
from .drafting import draft_reply
from .kb import KnowledgeBase
from .models import Category, Priority, Review, Status, Suggestion, TicketIn
from .ticketsystems import TicketSystem, WebhookError, build_systems

app = FastAPI(title="support-copilot (human-in-the-loop ticket triage)")
kb = KnowledgeBase()
conn = connect()
systems: dict[str, TicketSystem] = build_systems()


def _suggest(t: TicketIn) -> Suggestion:
    triage = classify(t.subject, t.body)
    articles = kb.search(f"{t.subject} {t.body}")
    return Suggestion(
        triage=triage,
        articles=[{k: a[k] for k in ("id", "title", "score")} for a in articles],
        draft_reply=draft_reply(t.subject, t.body, triage, articles, t.customer),
    )


def _store(t: TicketIn, s: Suggestion, system: str | None = None, external_id: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO tickets (created_at, subject, body, customer, suggestion, system, external_id)"
        " VALUES (?,?,?,?,?,?,?)",
        (now(), t.subject, t.body, t.customer, s.model_dump_json(), system, external_id),
    )
    conn.commit()
    return cur.lastrowid


def _push(system: TicketSystem, ext_id: str, kind: str, call, *args, **kwargs):
    """Call the ticket system and log it. An outage there must never lose our triage or the human's decision."""
    try:
        res = call(ext_id, *args, **kwargs)
        mode, payload, err = res["mode"], {"requests": res["requests"]}, None
    except Exception as e:
        mode, payload, err = "error", {"kind": kind}, str(e)
    conn.execute(
        "INSERT INTO outbox (created_at, system, external_id, kind, mode, payload, error) VALUES (?,?,?,?,?,?,?)",
        (now(), system.name, ext_id, kind, mode, json.dumps(payload), err),
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


@app.post("/webhooks/{system_name}", status_code=201)
async def ticket_system_webhook(system_name: str, request: Request):
    """Inbound webhook from a ticket system (zendesk, zammad). Configure it on 'ticket created'."""
    system = systems.get(system_name)
    if not system:
        raise HTTPException(404, f"unknown ticket system '{system_name}'")
    raw = await request.body()
    if not system.verify_webhook(request.headers, raw):
        raise HTTPException(401, "invalid webhook signature")
    try:
        inbound = system.parse_webhook(raw)
        t = TicketIn(subject=inbound.subject, body=inbound.body, customer=inbound.requester)
    except WebhookError as e:
        raise HTTPException(422, str(e))
    except ValidationError as e:
        raise HTTPException(422, json.loads(e.json()))

    # Ticket systems retry failed deliveries: make this idempotent per (system, external ticket).
    row = conn.execute("SELECT id FROM tickets WHERE system = ? AND external_id = ?",
                       (system.name, inbound.external_id)).fetchone()
    if row:
        return {"id": row["id"], "duplicate": True}

    s = _suggest(t)
    tid = _store(t, s, system.name, inbound.external_id)
    # Internal note only: the customer sees nothing until a human approves.
    _push(system, inbound.external_id, "triage_note", system.post_internal_note, _internal_note(s),
          priority=s.triage.priority, category=s.triage.category,
          tags=["ai_triaged", f"ai_cat_{s.triage.category.value}", f"ai_team_{s.triage.team}"])
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
    """Human decision. Approved replies go to the customer (tickets from a ticket system) as a public reply."""
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

    system = systems.get(row["system"]) if row["system"] else None
    sync = None
    if system:
        ext = row["external_id"]
        if r.action is Status.APPROVED:
            sync = _push(system, ext, "public_reply", system.post_public_reply, final_reply,
                         priority=Priority(final_pri), category=Category(final_cat))
        else:
            sync = _push(system, ext, "rejection_note", system.post_rejection, r.reviewer)
    return {"id": ticket_id, "status": r.action, "sync": sync}


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
    """Audit log of calls to ticket systems (sent / dry_run / error)."""
    rows = conn.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]
