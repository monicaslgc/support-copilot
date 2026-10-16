"""Write path: a ticket comes in, is triaged and drafted, stored and linked to related tickets, and the
ticket system is told (as an internal note only)."""
import json
from datetime import datetime

from fastapi import HTTPException
from pydantic import ValidationError

from . import clusters, db, guardrails, llm, queries, state
from .classifier import classify
from .drafting import draft
from .kb import tokens
from .llm import log
from .models import Flag, Sentiment, Status, Suggestion, TicketIn, Triage
from .ticketsystems import TicketSystem, WebhookError

SIMILARITY_SCAN = 200  # how many of the latest tickets are compared by text; the other rules use indexes


def suggest(t: TicketIn) -> Suggestion:
    """Triage, find the help article, draft the reply and check it."""
    triage = classify(t.subject, t.body)
    articles = state.kb.search(f"{t.subject} {t.body}")
    d = draft(t.subject, t.body, triage, articles, t.customer)
    notices = [d.notice] if d.notice else []
    if llm.enabled():  # the ticket text goes to the model with private details masked: tell the reviewer
        _, masked = guardrails.redact(f"{t.subject}\n{t.body}")
        if masked:
            notices.append(f"Masked before sending to the AI: {guardrails.describe(masked)}.")
    return Suggestion(
        triage=triage,
        articles=[{k: a[k] for k in ("id", "title", "score")} for a in articles],
        draft_reply=d.text,
        warnings=[Flag(**f) for f in guardrails.find_commitments(d.text)],
        notices=notices,
    )


def candidates(new: clusters.Item) -> list[clusters.Item]:
    """The few tickets the linking rules could match, found with indexes instead of scanning the whole window
    (which made every insert slower as the queue grew, while holding the lock). Caller holds the lock.

    The rules only need the *earliest* related ticket, so same-topic and same-customer ask for one row each;
    near-identical text is only searched among the most recent tickets.
    """
    cols = "id, cluster_id, customer, topic, category, subject, body, created_at"
    cutoff = (new.created_at - clusters.window()).isoformat(timespec="seconds")
    queries_ = []
    if new.topic is not None:
        queries_.append((f"SELECT {cols} FROM tickets WHERE created_at >= ? AND topic = ? AND category = ?"
                         " ORDER BY created_at, id LIMIT 1", (cutoff, new.topic, new.category)))
    if new.customer.strip() and new.customer.strip().lower() != clusters.UNKNOWN:
        queries_.append((f"SELECT {cols} FROM tickets WHERE created_at >= ? AND lower(customer) = lower(?)"
                         " AND category = ? ORDER BY created_at, id LIMIT 1",
                         (cutoff, new.customer.strip(), new.category)))
    queries_.append((f"SELECT {cols} FROM tickets WHERE created_at >= ? ORDER BY id DESC LIMIT ?",
                     (cutoff, SIMILARITY_SCAN)))
    rows = {r["id"]: r for sql, params in queries_ for r in state.conn.execute(sql, params)}
    return [
        clusters.Item(id=r["id"], cluster_id=r["cluster_id"], customer=r["customer"], topic=r["topic"],
                      category=r["category"], tokens=frozenset(tokens(f"{r['subject']} {r['body']}")),
                      created_at=datetime.fromisoformat(r["created_at"]))
        for r in rows.values()
    ]


def store(t: TicketIn, s: Suggestion, system: str | None = None, external_id: str | None = None) -> int | None:
    """Insert a ticket and link it to related ones. Returns None if (system, external_id) already exists
    (a concurrent duplicate webhook). Linking happens under the same lock as the insert, so two tickets
    arriving together always see each other."""
    created = db.now()
    topic = s.articles[0]["id"] if s.articles else None
    with state.lock:
        new = clusters.Item(id=0, customer=t.customer, category=s.triage.category.value, topic=topic,
                            tokens=frozenset(tokens(f"{t.subject} {t.body}")),
                            created_at=datetime.fromisoformat(created))
        others = candidates(new)
        parent = clusters.find_cluster(new, others)
        duplicate = clusters.find_duplicate(new, others)
        cur = state.conn.execute(
            "INSERT INTO tickets (created_at, subject, body, customer, suggestion, system, external_id,"
            " category, priority, topic, cluster_id, duplicate_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (system, external_id) DO NOTHING",  # not OR IGNORE: that would also hide NOT NULL errors
            (created, t.subject, t.body, t.customer, s.model_dump_json(), system, external_id,
             s.triage.category.value, s.triage.priority.value, topic,
             (parent.cluster_id or parent.id) if parent else None, duplicate.id if duplicate else None),
        )
        if cur.rowcount and parent and parent.cluster_id is None:
            state.conn.execute("UPDATE tickets SET cluster_id = ? WHERE id = ?", (parent.id, parent.id))
        state.conn.commit()
        return cur.lastrowid if cur.rowcount else None


def push(system: TicketSystem, ext_id: str, kind: str, call, *args, **kwargs):
    """Call the ticket system and log it. An outage there must never lose our triage or the human's decision."""
    try:
        res = call(ext_id, *args, **kwargs)
        mode, payload, err = res["mode"], {"requests": res["requests"]}, None
    except Exception as e:  # noqa: BLE001 - any failure is recorded in the outbox, never raised to the caller
        log.warning("%s %s failed for %s: %s", system.name, kind, ext_id, e)
        mode, payload, err = "error", {"kind": kind}, str(e)
    with state.lock:
        state.conn.execute(
            "INSERT INTO outbox (created_at, system, external_id, kind, mode, payload, error) VALUES (?,?,?,?,?,?,?)",
            (db.now(), system.name, ext_id, kind, mode, json.dumps(payload), err),
        )
        state.conn.commit()
    return mode


def context(tid: int) -> dict:
    """What we know about how this ticket relates to others, for the internal note and tags."""
    row = state.fetch_one("SELECT cluster_id, duplicate_of, system, customer FROM tickets WHERE id = ?", (tid,))
    cluster = queries.cluster_stats([row["cluster_id"]]).get(row["cluster_id"]) if row and row["cluster_id"] else None
    duplicate = None
    if row and row["duplicate_of"]:
        d = state.fetch_one("SELECT id, system, external_id FROM tickets WHERE id = ?", (row["duplicate_of"],))
        if d:
            same_system = d["system"] and d["system"] == row["system"]
            duplicate = f"{d['system']} ticket {d['external_id']}" if same_system else f"ticket #{d['id']}"
    return {"cluster": cluster, "duplicate_of": duplicate,
            "history": queries.customer_history(row["customer"], tid) if row else {"count": 0, "recent": []}}


def context_tags(ctx: dict, triage: Triage) -> list[str]:
    return ((["ai_possible_duplicate"] if ctx["duplicate_of"] else [])
            + (["ai_spike"] if ctx["cluster"] and ctx["cluster"]["kind"] == "spike" else [])
            + (["ai_sentiment_negative"] if triage.sentiment is Sentiment.NEGATIVE else [])
            + ([f"ai_lang_{triage.language}"] if triage.language != "und" else []))


def internal_note(s: Suggestion, ctx: dict | None = None) -> str:
    tr = s.triage
    arts = ", ".join(a["title"] for a in s.articles) or "none"
    lines = []
    if ctx and ctx["duplicate_of"]:
        lines.append(f"Possible duplicate of {ctx['duplicate_of']} (same customer and category, close in time).")
    if c := ctx and ctx["cluster"]:
        what = f'"{c["topic_title"]}"' if c["topic_title"] else "the same issue"
        lead = "POSSIBLE INCIDENT" if c["kind"] == "spike" else "Related"
        lines.append(f"{lead}: {c['size']} tickets from {c['customers']} customers about {what} in a short window.")
    if s.warnings:
        lines.append(f"Check before approving: the draft {guardrails.explain([w.model_dump() for w in s.warnings])}.")
    lines.extend(s.notices)
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


def receive_webhook(system: TicketSystem, headers, raw: bytes) -> dict:
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
        return state.fetch_one("SELECT id FROM tickets WHERE system = ? AND external_id = ?",
                               (system.name, inbound.external_id))

    # Ticket systems retry failed deliveries: make this idempotent per (system, external ticket).
    if row := existing():
        return {"id": row["id"], "duplicate": True}
    s = suggest(t)
    tid = store(t, s, system.name, inbound.external_id)
    if tid is None:  # an identical webhook finished first while we were triaging
        return {"id": existing()["id"], "duplicate": True}
    ctx = context(tid)
    # Internal note only: the customer sees nothing until a human approves.
    push(system, inbound.external_id, "triage_note", system.post_internal_note, internal_note(s, ctx),
         priority=s.triage.priority, category=s.triage.category,
         tags=["ai_triaged", f"ai_cat_{s.triage.category.value}", f"ai_team_{s.triage.team}",
               *context_tags(ctx, s.triage)])
    return {"id": tid, "status": Status.PENDING_REVIEW, "suggestion": s}
