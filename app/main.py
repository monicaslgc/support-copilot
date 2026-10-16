import hmac
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import ValidationError

from . import clusters, feedback, llm
from .classifier import classify
from .db import connect, now, row_to_ticket
from .drafting import draft_reply
from .kb import KnowledgeBase, tokens
from .models import Category, Decision, Priority, Review, Sentiment, Status, Suggestion, TicketIn, Triage
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


def _attach_reviews(tickets: list[dict]) -> list[dict]:
    """Add `review` (what the human decided and the reply that was actually approved) to each ticket."""
    ids = [t["id"] for t in tickets]
    by_ticket = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in _all(f"SELECT ticket_id, reviewer, action, final_category, final_priority, final_reply, created_at,"
                      f" reasons, note FROM reviews WHERE ticket_id IN ({marks})", tuple(ids)):
            by_ticket[r["ticket_id"]] = {k: r[k] for k in r.keys() if k != "ticket_id"}
            by_ticket[r["ticket_id"]]["reasons"] = json.loads(r["reasons"] or "[]")
    for t in tickets:
        t["review"] = by_ticket.get(t["id"])
    return tickets


def _cluster_stats(ids) -> dict[int, dict]:
    """Size, distinct customers and kind for each cluster of 2+ tickets. kind is "spike" (a possible
    incident) when enough different customers are affected, else "related"."""
    ids = [i for i in set(ids) if i is not None]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = _all(
        "SELECT cluster_id, COUNT(*) AS size,"
        " COUNT(DISTINCT CASE WHEN lower(customer) = 'unknown' THEN 'unknown-' || id ELSE lower(customer) END)"
        " AS customers, MIN(created_at) AS first_seen, MAX(created_at) AS last_seen,"
        " SUM(status = 'pending_review') AS pending, MAX(topic) AS topic"
        f" FROM tickets WHERE cluster_id IN ({marks}) GROUP BY cluster_id", tuple(ids))
    out = {}
    for r in rows:
        if r["size"] < 2:
            continue
        out[r["cluster_id"]] = {
            "id": r["cluster_id"], "size": r["size"], "customers": r["customers"],
            "kind": "spike" if r["customers"] >= clusters.spike_min_customers() else "related",
            "topic": r["topic"], "topic_title": kb.title(r["topic"]),
            "first_seen": r["first_seen"], "last_seen": r["last_seen"], "pending": r["pending"] or 0,
        }
    return out


def _attach_clusters(tickets: list[dict]) -> list[dict]:
    """Add `cluster` (null, or the stats of the group of tickets about the same issue) to each ticket."""
    stats = _cluster_stats([t.get("cluster_id") for t in tickets])
    for t in tickets:
        t["cluster"] = stats.get(t.get("cluster_id"))
    return tickets


def _customer_history(customer: str, before_id: int, limit: int = 5) -> dict:
    """The same customer's earlier tickets (by the customer string the ticket system sent: a name or an
    email, so two people with the same name are indistinguishable). "unknown" has no history."""
    if not customer.strip() or customer.strip().lower() == clusters.UNKNOWN:
        return {"count": 0, "recent": []}
    where, params = "lower(customer) = lower(?) AND id < ?", (customer.strip(), before_id)
    count = _one(f"SELECT COUNT(*) AS n FROM tickets WHERE {where}", params)["n"]
    recent = []
    for r in _all(f"SELECT id, subject, status, created_at, suggestion FROM tickets WHERE {where}"
                  " ORDER BY id DESC LIMIT ?", (*params, limit)):
        tr = json.loads(r["suggestion"]).get("triage", {})
        recent.append({"id": r["id"], "subject": r["subject"], "status": r["status"], "created_at": r["created_at"],
                       "category": tr.get("category"), "priority": tr.get("priority")})
    return {"count": count, "recent": recent}


def _attach_history(tickets: list[dict]) -> list[dict]:
    for t in tickets:
        t["history"] = _customer_history(t["customer"], t["id"])
    return tickets


def require_api_key(x_api_key: str | None = Header(default=None)):
    """Protects the review/ops API. Webhooks are authenticated by each system's own signature instead.

    If API_KEY is not set the API is open (local development): set it anywhere reachable by others.
    """
    expected = os.getenv("API_KEY")
    if expected and not hmac.compare_digest((x_api_key or "").encode(), expected.encode()):
        raise HTTPException(401, "missing or invalid X-API-Key")


api = APIRouter(dependencies=[Depends(require_api_key)])


@app.get("/", include_in_schema=False)
def review_ui():
    """The review queue page. It holds no data itself: it calls the (API-key protected) API."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


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


def _recent_items(new_at: datetime) -> list[clusters.Item]:
    """Tickets created within the clustering window before `new_at`. Caller holds the lock."""
    cutoff = (new_at - clusters.window()).isoformat(timespec="seconds")
    return [
        clusters.Item(
            id=r["id"], cluster_id=r["cluster_id"], customer=r["customer"], topic=r["topic"],
            category=json.loads(r["suggestion"])["triage"]["category"],
            tokens=frozenset(tokens(f"{r['subject']} {r['body']}")), created_at=datetime.fromisoformat(r["created_at"]))
        for r in conn.execute(
            "SELECT id, cluster_id, customer, topic, subject, body, created_at, suggestion"
            " FROM tickets WHERE created_at >= ?", (cutoff,))
    ]


def _store(t: TicketIn, s: Suggestion, system: str | None = None, external_id: str | None = None) -> int | None:
    """Insert a ticket and link it to related ones. Returns None if (system, external_id) already exists
    (a concurrent duplicate webhook). Linking happens under the same lock as the insert, so two tickets
    arriving together always see each other."""
    created = now()
    topic = s.articles[0]["id"] if s.articles else None
    with _write_lock:
        new = clusters.Item(id=0, customer=t.customer, category=s.triage.category.value, topic=topic,
                            tokens=frozenset(tokens(f"{t.subject} {t.body}")),
                            created_at=datetime.fromisoformat(created))
        others = _recent_items(new.created_at)
        parent = clusters.find_cluster(new, others)
        duplicate = clusters.find_duplicate(new, others)
        cur = conn.execute(
            "INSERT OR IGNORE INTO tickets (created_at, subject, body, customer, suggestion, system, external_id,"
            " topic, cluster_id, duplicate_of) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (created, t.subject, t.body, t.customer, s.model_dump_json(), system, external_id, topic,
             (parent.cluster_id or parent.id) if parent else None, duplicate.id if duplicate else None),
        )
        if cur.rowcount and parent and parent.cluster_id is None:
            conn.execute("UPDATE tickets SET cluster_id = ? WHERE id = ?", (parent.id, parent.id))
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


def _context(tid: int) -> dict:
    """What we know about how this ticket relates to others, for the internal note and tags."""
    row = _one("SELECT cluster_id, duplicate_of, system, customer FROM tickets WHERE id = ?", (tid,))
    cluster = _cluster_stats([row["cluster_id"]]).get(row["cluster_id"]) if row and row["cluster_id"] else None
    duplicate = None
    if row and row["duplicate_of"]:
        d = _one("SELECT id, system, external_id FROM tickets WHERE id = ?", (row["duplicate_of"],))
        if d:
            same_system = d["system"] and d["system"] == row["system"]
            duplicate = f"{d['system']} ticket {d['external_id']}" if same_system else f"ticket #{d['id']}"
    return {"cluster": cluster, "duplicate_of": duplicate,
            "history": _customer_history(row["customer"], tid) if row else {"count": 0, "recent": []}}


def _context_tags(ctx: dict, triage: Triage) -> list[str]:
    return ((["ai_possible_duplicate"] if ctx["duplicate_of"] else [])
            + (["ai_spike"] if ctx["cluster"] and ctx["cluster"]["kind"] == "spike" else [])
            + (["ai_sentiment_negative"] if triage.sentiment is Sentiment.NEGATIVE else [])
            + ([f"ai_lang_{triage.language}"] if triage.language != "und" else []))


def _internal_note(s: Suggestion, ctx: dict | None = None) -> str:
    tr = s.triage
    arts = ", ".join(a["title"] for a in s.articles) or "none"
    lines = []
    if ctx and ctx["duplicate_of"]:
        lines.append(f"Possible duplicate of {ctx['duplicate_of']} (same customer and category, close in time).")
    if c := ctx and ctx["cluster"]:
        what = f'"{c["topic_title"]}"' if c["topic_title"] else "the same issue"
        lead = "POSSIBLE INCIDENT" if c["kind"] == "spike" else "Related"
        lines.append(f"{lead}: {c['size']} tickets from {c['customers']} customers about {what} in a short window.")
    if ctx and ctx.get("history", {}).get("count"):
        h = ctx["history"]
        lines.append(f"Repeat customer: {h['count']} earlier ticket{'s' if h['count'] != 1 else ''}"
                     f" (latest: \"{h['recent'][0]['subject'][:60]}\", {h['recent'][0]['status']}).")
    related = ("\n".join(lines) + "\n") if lines else ""
    return (
        f"[AI triage - pending human review]\n"
        f"Category: {tr.category.value} | Priority: {tr.priority.value} | Team: {tr.team} "
        f"| Confidence: {tr.confidence} ({tr.source})\n"
        f"Sentiment: {tr.sentiment.value} | Language: {tr.language}\n"
        f"Why: {tr.rationale}\n{related}KB articles: {arts}\n\nSuggested reply:\n{s.draft_reply}"
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
    ctx = _context(tid)
    # Internal note only: the customer sees nothing until a human approves.
    _push(system, inbound.external_id, "triage_note", system.post_internal_note, _internal_note(s, ctx),
          priority=s.triage.priority, category=s.triage.category,
          tags=["ai_triaged", f"ai_cat_{s.triage.category.value}", f"ai_team_{s.triage.team}",
                *_context_tags(ctx, s.triage)])
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
    return _attach_history(_attach_clusters(_attach_reviews(tickets[:limit])))


@api.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    row = _one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    if not row:
        raise HTTPException(404, "ticket not found")
    return _attach_history(_attach_clusters(_attach_reviews([row_to_ticket(row)])))[0]


def _apply_review(ticket_id: int, r: Review) -> dict:
    """Record a human decision. For tickets from a ticket system, an approved reply goes to the customer
    as a public reply."""
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
            " final_reply, ai_category, ai_priority, ai_reply, reasons, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ticket_id, now(), r.reviewer, r.action.value, final_cat, final_pri, final_reply,
             ai["category"], ai["priority"], s["draft_reply"], json.dumps([x.value for x in r.reasons]), r.note),
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


@api.post("/tickets/{ticket_id}/review")
def review_ticket(ticket_id: int, r: Review):
    return _apply_review(ticket_id, r)


@api.get("/clusters")
def list_clusters():
    """Groups of tickets about the same issue that still have something to review: possible incidents first."""
    ids = [r["cluster_id"] for r in _all(
        "SELECT DISTINCT cluster_id FROM tickets WHERE cluster_id IS NOT NULL AND status = 'pending_review'")]
    out = []
    for c in _cluster_stats(ids).values():
        rows = _all("SELECT id, status FROM tickets WHERE cluster_id = ? ORDER BY id", (c["id"],))
        out.append({**c, "ticket_ids": [r["id"] for r in rows],
                    "pending_ids": [r["id"] for r in rows if r["status"] == Status.PENDING_REVIEW.value]})
    out.sort(key=lambda c: (c["kind"] != "spike", -c["size"], c["id"]))
    return out


@api.post("/clusters/{cluster_id}/review")
def review_cluster(cluster_id: int, r: Review):
    """One decision for every pending ticket of a possible incident. Without `edited_reply` each ticket keeps
    its own draft; with it, the same text goes to all of them. Each ticket is recorded as its own review."""
    c = _cluster_stats([cluster_id]).get(cluster_id)
    if not c:
        raise HTTPException(404, "cluster not found")
    if c["kind"] != "spike":
        raise HTTPException(409, "bulk review is only for possible incidents (several different customers)")
    pending = [t["id"] for t in _all("SELECT id FROM tickets WHERE cluster_id = ? AND status = ? ORDER BY id",
                                     (cluster_id, Status.PENDING_REVIEW.value))]
    if not pending:
        raise HTTPException(409, "nothing pending in this cluster")
    reviewed, skipped, results = [], [], []
    for tid in pending:
        try:
            results.append(_apply_review(tid, r))
            reviewed.append(tid)
        except HTTPException as e:
            if e.status_code != 409:  # someone reviewed it a moment ago: skip it, keep going
                raise
            skipped.append(tid)
    return {"cluster": cluster_id, "reviewed": reviewed, "skipped": skipped, "results": results}


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


@api.get("/metrics/breakdown")
def metrics_breakdown():
    """Quality of the AI per category, why humans corrected it, and where it may have earned more autonomy."""
    return feedback.breakdown(_all(
        "SELECT action, ai_category, ai_priority, ai_reply, final_category, final_priority, final_reply, reasons"
        " FROM reviews"))


@api.get("/outbox")
def outbox(limit: int = Query(50, ge=1, le=500)):
    """Audit log of calls to ticket systems (sent / dry_run / error)."""
    rows = _all("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]


app.include_router(api)
