import hmac
import json
import logging
import os
import threading

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from . import llm
from .classifier import classify
from .db import connect, now, row_to_ticket
from .drafting import draft_reply
from .kb import KnowledgeBase
from .models import Category, Decision, Priority, Review, Status, Suggestion, TicketIn
from .ticketsystems import TicketSystem, WebhookError, build_systems

log = logging.getLogger("support_copilot")

app = FastAPI(title="support-copilot (human-in-the-loop ticket triage)")
kb = KnowledgeBase()
conn = connect()
systems: dict[str, TicketSystem] = build_systems()

# One SQLite connection is shared by the worker threads and it is not safe for simultaneous use, reads
# included (concurrent calls raise InterfaceError). Every DB access takes this lock; it is never held
# across a network or model call.
_write_lock = threading.RLock()
MAX_WEBHOOK_BYTES = 1_000_000


def _all(sql: str, params: tuple = ()) -> list:
    with _write_lock:
        return conn.execute(sql, params).fetchall()


def _one(sql: str, params: tuple = ()):
    with _write_lock:
        return conn.execute(sql, params).fetchone()


def require_api_key(x_api_key: str | None = Header(default=None)):
    """Protects the review/ops API. Webhooks are authenticated by each system's own signature instead.

    If API_KEY is not set the API is open (local development): set it anywhere reachable by others.
    """
    expected = os.getenv("API_KEY")
    if expected and not hmac.compare_digest((x_api_key or "").encode(), expected.encode()):
        raise HTTPException(401, "missing or invalid X-API-Key")


api = APIRouter(dependencies=[Depends(require_api_key)])


@app.get("/health")
def health():
    return {"status": "ok", "llm": llm.enabled(), "model": llm.model_name() if llm.enabled() else None,
            "ticket_systems": {n: s.configured for n, s in systems.items()}}


def _suggest(t: TicketIn) -> Suggestion:
    triage = classify(t.subject, t.body)
    articles = kb.search(f"{t.subject} {t.body}")
    return Suggestion(
        triage=triage,
        articles=[{k: a[k] for k in ("id", "title", "score")} for a in articles],
        draft_reply=draft_reply(t.subject, t.body, triage, articles, t.customer),
    )


def _store(t: TicketIn, s: Suggestion, system: str | None = None, external_id: str | None = None) -> int | None:
    """Insert a ticket. Returns None if (system, external_id) already exists (a concurrent duplicate webhook)."""
    with _write_lock:
        cur = conn.execute(
            "INSERT OR IGNORE INTO tickets (created_at, subject, body, customer, suggestion, system, external_id)"
            " VALUES (?,?,?,?,?,?,?)",
            (now(), t.subject, t.body, t.customer, s.model_dump_json(), system, external_id),
        )
        conn.commit()
        return cur.lastrowid if cur.rowcount else None


def _push(system: TicketSystem, ext_id: str, kind: str, call, *args, **kwargs):
    """Call the ticket system and log it. An outage there must never lose our triage or the human's decision."""
    try:
        res = call(ext_id, *args, **kwargs)
        mode, payload, err = res["mode"], {"requests": res["requests"]}, None
    except Exception as e:  # noqa: BLE001 - any failure is recorded in the outbox, never raised to the caller
        log.warning("%s %s failed for %s: %s", system.name, kind, ext_id, e)
        mode, payload, err = "error", {"kind": kind}, str(e)
    with _write_lock:
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


@api.post("/tickets", status_code=201)
def create_ticket(t: TicketIn):
    """Triage a ticket with AI. Nothing is sent: it waits for human review."""
    s = _suggest(t)
    return {"id": _store(t, s), "status": Status.PENDING_REVIEW, "suggestion": s}


def _ingest(system: TicketSystem, headers, raw: bytes) -> dict:
    """Blocking work for one webhook (LLM, DB, ticket-system calls). Runs in a worker thread."""
    if not system.verify_webhook(headers, raw):
        raise HTTPException(401, "invalid webhook signature")
    try:
        inbound = system.parse_webhook(raw)
        t = TicketIn(subject=inbound.subject, body=inbound.body, customer=inbound.requester)
    except WebhookError as e:
        raise HTTPException(422, str(e)) from e
    except ValidationError as e:
        raise HTTPException(422, json.loads(e.json())) from e

    def existing():
        return _one("SELECT id FROM tickets WHERE system = ? AND external_id = ?", (system.name, inbound.external_id))

    # Ticket systems retry failed deliveries: make this idempotent per (system, external ticket).
    if row := existing():
        return {"id": row["id"], "duplicate": True}
    s = _suggest(t)
    tid = _store(t, s, system.name, inbound.external_id)
    if tid is None:  # an identical webhook finished first while we were triaging
        return {"id": existing()["id"], "duplicate": True}
    # Internal note only: the customer sees nothing until a human approves.
    _push(system, inbound.external_id, "triage_note", system.post_internal_note, _internal_note(s),
          priority=s.triage.priority, category=s.triage.category,
          tags=["ai_triaged", f"ai_cat_{s.triage.category.value}", f"ai_team_{s.triage.team}"])
    return {"id": tid, "status": Status.PENDING_REVIEW, "suggestion": s}


@app.post("/webhooks/{system_name}", status_code=201)
async def ticket_system_webhook(system_name: str, request: Request):
    """Inbound webhook from a ticket system (zendesk, zammad). Configure it on 'ticket created'."""
    system = systems.get(system_name)
    if not system:
        raise HTTPException(404, f"unknown ticket system '{system_name}'")
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "webhook body too large")
    # The model and ticket-system calls block for seconds: keep them off the event loop.
    return await run_in_threadpool(_ingest, system, request.headers, raw)


@api.get("/tickets")
def list_tickets(status: Status | None = None, limit: int = Query(100, ge=1, le=500)):
    """Review queue, most urgent first."""
    q = "SELECT * FROM tickets" + (" WHERE status = ?" if status else "")
    rows = _all(q, (status.value,) if status else ())
    tickets = [row_to_ticket(r) for r in rows]
    tickets.sort(key=lambda t: (t["suggestion"]["triage"]["priority"], t["created_at"]))
    return tickets[:limit]


@api.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    row = _one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    if not row:
        raise HTTPException(404, "ticket not found")
    return row_to_ticket(row)


@api.post("/tickets/{ticket_id}/review")
def review_ticket(ticket_id: int, r: Review):
    """Human decision. Approved replies go to the customer (tickets from a ticket system) as a public reply."""
    row = _one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    if not row:
        raise HTTPException(404, "ticket not found")
    s = json.loads(row["suggestion"])
    ai = s["triage"]
    final_cat = r.final_category.value if r.final_category else ai["category"]
    final_pri = r.final_priority.value if r.final_priority else ai["priority"]
    final_reply = r.edited_reply or s["draft_reply"]
    with _write_lock:
        # Atomic claim: of two simultaneous reviews exactly one changes the row, the other gets a 409.
        claimed = conn.execute("UPDATE tickets SET status = ? WHERE id = ? AND status = ?",
                               (r.action.value, ticket_id, Status.PENDING_REVIEW.value)).rowcount
        if not claimed:
            conn.rollback()
            raise HTTPException(409, "ticket already reviewed")
        conn.execute(
            "INSERT INTO reviews (ticket_id, created_at, reviewer, action, final_category, final_priority,"
            " final_reply, ai_category, ai_priority, ai_reply) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ticket_id, now(), r.reviewer, r.action.value, final_cat, final_pri, final_reply,
             ai["category"], ai["priority"], s["draft_reply"]),
        )
        conn.commit()

    system = systems.get(row["system"]) if row["system"] else None
    sync = None
    if system:
        ext = row["external_id"]
        if r.action is Decision.APPROVED:
            sync = _push(system, ext, "public_reply", system.post_public_reply, final_reply,
                         priority=Priority(final_pri), category=Category(final_cat))
        else:
            sync = _push(system, ext, "rejection_note", system.post_rejection, r.reviewer)
    return {"id": ticket_id, "status": r.action, "sync": sync}


@api.get("/metrics")
def metrics():
    """How often humans agree with the AI: the number that decides whether to automate more."""
    rows = _all("SELECT * FROM reviews WHERE action = 'approved'")
    n = len(rows)
    if not n:
        return {"reviewed": 0}
    return {
        "reviewed": n,
        "category_agreement": round(sum(r["ai_category"] == r["final_category"] for r in rows) / n, 2),
        "priority_agreement": round(sum(r["ai_priority"] == r["final_priority"] for r in rows) / n, 2),
        "reply_edit_rate": round(sum(r["ai_reply"] != r["final_reply"] for r in rows) / n, 2),
    }


@api.get("/outbox")
def outbox(limit: int = Query(50, ge=1, le=500)):
    """Audit log of calls to ticket systems (sent / dry_run / error)."""
    rows = _all("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]


app.include_router(api)
