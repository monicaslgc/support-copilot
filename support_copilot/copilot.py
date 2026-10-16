"""The Copilot: everything that happens to a ticket, with no web framework in sight.

    cp = Copilot(db="tickets.db", kb="path/to/your/articles")
    suggestion = cp.suggest(TicketIn(subject=..., body=..., customer=...))   # triage + draft, nothing stored
    result = cp.submit(ticket)                                                # ... and queued for human review
    cp.review(result["id"], Review(action="approved", reviewer="ana"))

`support_copilot.api.create_router(cp)` exposes the same operations over HTTP for FastAPI apps.
"""
import hmac
import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from . import clusters, feedback, guardrails, llm
from . import db as database
from .classifier import classify
from .config import Taxonomy
from .drafting import draft
from .kb import KnowledgeBase, tokens
from .llm import log
from .models import Category, Decision, Flag, Priority, Review, Sentiment, Status, Suggestion, TicketIn, Triage
from .ticketsystems import TicketSystem, WebhookError, build_systems

SIMILARITY_SCAN = 200  # how many of the latest tickets are compared by text; the other rules use indexes
MAX_WEBHOOK_BYTES = 1_000_000


class CopilotError(Exception):
    """A request the Copilot refuses. `status` is the HTTP code the router answers with."""

    def __init__(self, status: int, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


class Copilot:
    """Triage, drafting, the review queue and the ticket-system adapters, around one database.

    db       a path, ":memory:" (default), or a sqlite3 connection you opened (tables are created if missing)
    kb       a folder of markdown help articles, or a KnowledgeBase; default is a small bundled sample
    systems  ticket-system adapters by name; default is Zendesk and Zammad configured from the environment
    api_key  required as `X-API-Key` by the HTTP router; default is the API_KEY environment variable
    taxonomy the tags and names written to your helpdesk, and the tags that approve or reject from inside it
    allowed_reviewers  emails or names who may approve or reject by tag in the helpdesk; default is anyone the
                       helpdesk lets edit the ticket. Compared without regard to case.
    allow_unsigned_decisions  a decision by tag sends a reply to a customer, so for a connected ticket system it is
                       only accepted when that system's webhook secret is set (the signature is what proves the tag
                       came from your helpdesk). True accepts it without; dry-run systems never need it.

    Thread-safe: one SQLite connection is shared and every access takes `lock`, which is never held across
    a network or model call. Read `copilot.conn` and `copilot.systems` as attributes at call time.
    """

    def __init__(self, db: str | os.PathLike | sqlite3.Connection | None = ":memory:",
                 kb: str | os.PathLike | KnowledgeBase | None = None,
                 systems: dict[str, TicketSystem] | None = None, api_key: str | None = None,
                 taxonomy: Taxonomy | None = None, allowed_reviewers: set[str] | frozenset[str] | None = None,
                 allow_unsigned_decisions: bool = False):
        self.conn = (database.init(db) if isinstance(db, sqlite3.Connection)
                     else database.connect(None if db is None else str(db)))
        self.kb = kb if isinstance(kb, KnowledgeBase) else KnowledgeBase(Path(kb)) if kb else KnowledgeBase()
        self.systems = systems if systems is not None else build_systems()
        self.api_key = api_key
        self.taxonomy = taxonomy or Taxonomy()
        self.allowed_reviewers = {r.strip().lower() for r in allowed_reviewers} if allowed_reviewers else None
        self.allow_unsigned_decisions = allow_unsigned_decisions
        self.lock = threading.RLock()

    # -- access ------------------------------------------------------------------------------------------

    def fetch_all(self, sql: str, params: tuple = ()) -> list:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def fetch_one(self, sql: str, params: tuple = ()):
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def check_api_key(self, supplied: str | None) -> bool:
        """True if the review/ops API may be used. With no key configured the API is open (local development)."""
        expected = self.api_key or os.getenv("API_KEY")
        return not expected or hmac.compare_digest((supplied or "").encode(), expected.encode())

    def security_warnings(self) -> list[str]:
        """Settings that leave a live deployment open. Only matters once a ticket system is connected, because
        that is when real customer data starts to flow."""
        out = [f"{name} is connected but {s.secret_env} is not set: anyone who can reach this server can post "
               f"tickets to its webhook" for name, s in self.systems.items()
               if s.configured and not os.getenv(s.secret_env)]
        if any(s.configured for s in self.systems.values()) and not (self.api_key or os.getenv("API_KEY")):
            out.append("API_KEY is not set: the review API, which returns customer messages, is open")
        return out

    def log_startup_warnings(self) -> None:
        for w in self.security_warnings():
            log.warning(w)

    # -- triage and drafting (nothing is stored or sent) -------------------------------------------------

    def suggest(self, t: TicketIn) -> Suggestion:
        """Triage, find the help article, draft the reply and check it."""
        triage = classify(t.subject, t.body)
        articles = self.kb.search(f"{t.subject} {t.body}")
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

    # -- the review queue --------------------------------------------------------------------------------

    def submit(self, t: TicketIn) -> dict:
        """Triage a ticket and queue it. Nothing is sent: it waits for human review."""
        s = self.suggest(t)
        return {"id": self.store(t, s), "status": Status.PENDING_REVIEW, "suggestion": s}

    def _candidates(self, new: clusters.Item) -> list[clusters.Item]:
        """The few tickets the linking rules could match, found with indexes instead of scanning the whole window
        (which made every insert slower as the queue grew, while holding the lock). Caller holds the lock.

        The rules only need the *earliest* related ticket, so same-topic and same-customer ask for one row each;
        near-identical text is only searched among the most recent tickets.
        """
        cols = "id, cluster_id, customer, topic, category, subject, body, created_at"
        cutoff = (new.created_at - clusters.window()).isoformat(timespec="seconds")
        found = []
        if new.topic is not None:
            found.append((f"SELECT {cols} FROM tickets WHERE created_at >= ? AND topic = ? AND category = ?"
                          " ORDER BY created_at, id LIMIT 1", (cutoff, new.topic, new.category)))
        if new.customer.strip() and new.customer.strip().lower() != clusters.UNKNOWN:
            found.append((f"SELECT {cols} FROM tickets WHERE created_at >= ? AND lower(customer) = lower(?)"
                          " AND category = ? ORDER BY created_at, id LIMIT 1",
                          (cutoff, new.customer.strip(), new.category)))
        found.append((f"SELECT {cols} FROM tickets WHERE created_at >= ? ORDER BY id DESC LIMIT ?",
                      (cutoff, SIMILARITY_SCAN)))
        rows = {r["id"]: r for sql, params in found for r in self.conn.execute(sql, params)}
        return [
            clusters.Item(id=r["id"], cluster_id=r["cluster_id"], customer=r["customer"], topic=r["topic"],
                          category=r["category"], tokens=frozenset(tokens(f"{r['subject']} {r['body']}")),
                          created_at=datetime.fromisoformat(r["created_at"]))
            for r in rows.values()
        ]

    def store(self, t: TicketIn, s: Suggestion, system: str | None = None,
              external_id: str | None = None) -> int | None:
        """Insert a ticket and link it to related ones. Returns None if (system, external_id) already exists
        (a concurrent duplicate webhook). Linking happens under the same lock as the insert, so two tickets
        arriving together always see each other."""
        created = database.now()
        topic = s.articles[0]["id"] if s.articles else None
        with self.lock:
            new = clusters.Item(id=0, customer=t.customer, category=s.triage.category.value, topic=topic,
                                tokens=frozenset(tokens(f"{t.subject} {t.body}")),
                                created_at=datetime.fromisoformat(created))
            others = self._candidates(new)
            parent = clusters.find_cluster(new, others)
            duplicate = clusters.find_duplicate(new, others)
            cur = self.conn.execute(
                "INSERT INTO tickets (created_at, subject, body, customer, suggestion, system, external_id,"
                " category, priority, topic, cluster_id, duplicate_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (system, external_id) DO NOTHING",  # not OR IGNORE: it would also hide NOT NULL errors
                (created, t.subject, t.body, t.customer, s.model_dump_json(), system, external_id,
                 s.triage.category.value, s.triage.priority.value, topic,
                 (parent.cluster_id or parent.id) if parent else None, duplicate.id if duplicate else None),
            )
            if cur.rowcount and parent and parent.cluster_id is None:
                self.conn.execute("UPDATE tickets SET cluster_id = ? WHERE id = ?", (parent.id, parent.id))
            self.conn.commit()
            return cur.lastrowid if cur.rowcount else None

    def list_tickets(self, status: Status | None = None, limit: int = 100) -> list[dict]:
        """Review queue, most urgent first."""
        status = Status(status) if status else None
        where, params = ("WHERE status = ? ", (status.value,)) if status else ("", ())
        rows = self.fetch_all(f"SELECT * FROM tickets {where}ORDER BY priority, created_at, id LIMIT ?",
                              (*params, limit))
        return self.enrich([database.row_to_ticket(r) for r in rows])

    def get_ticket(self, ticket_id: int) -> dict:
        row = self.fetch_one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        if not row:
            raise CopilotError(404, "ticket not found")
        return self.enrich([database.row_to_ticket(row)])[0]

    # -- what is shown next to a ticket ------------------------------------------------------------------

    def attach_reviews(self, tickets: list[dict]) -> list[dict]:
        """Add `review` (what the human decided and the reply that was actually approved) to each ticket."""
        ids = [t["id"] for t in tickets]
        by_ticket = {}
        if ids:
            marks = ",".join("?" * len(ids))
            for r in self.fetch_all(
                    "SELECT ticket_id, reviewer, action, final_category, final_priority, final_reply, created_at,"
                    f" reasons, note FROM reviews WHERE ticket_id IN ({marks})", tuple(ids)):
                by_ticket[r["ticket_id"]] = {k: r[k] for k in r.keys() if k != "ticket_id"}
                by_ticket[r["ticket_id"]]["reasons"] = json.loads(r["reasons"] or "[]")
        for t in tickets:
            t["review"] = by_ticket.get(t["id"])
        return tickets

    def cluster_stats(self, ids) -> dict[int, dict]:
        """Size, distinct customers and kind for each cluster of 2+ tickets. kind is "spike" (a possible
        incident) when enough different customers are affected, else "related"."""
        ids = [i for i in set(ids) if i is not None]
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.fetch_all(
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
                "topic": r["topic"], "topic_title": self.kb.title(r["topic"]),
                "first_seen": r["first_seen"], "last_seen": r["last_seen"], "pending": r["pending"] or 0,
            }
        return out

    def attach_clusters(self, tickets: list[dict]) -> list[dict]:
        """Add `cluster` (null, or the stats of the group of tickets about the same issue) to each ticket."""
        stats = self.cluster_stats([t.get("cluster_id") for t in tickets])
        for t in tickets:
            t["cluster"] = stats.get(t.get("cluster_id"))
        return tickets

    def customer_history(self, customer: str, before_id: int, limit: int = 5) -> dict:
        """The same customer's earlier tickets (by the customer string the ticket system sent: a name or an
        email, so two people with the same name are indistinguishable). "unknown" has no history."""
        if not customer.strip() or customer.strip().lower() == clusters.UNKNOWN:
            return {"count": 0, "recent": []}
        where, params = "lower(customer) = lower(?) AND id < ?", (customer.strip(), before_id)
        count = self.fetch_one(f"SELECT COUNT(*) AS n FROM tickets WHERE {where}", params)["n"]
        recent = []
        for r in self.fetch_all(f"SELECT id, subject, status, created_at, suggestion FROM tickets WHERE {where}"
                                " ORDER BY id DESC LIMIT ?", (*params, limit)):
            tr = json.loads(r["suggestion"]).get("triage", {})
            recent.append({"id": r["id"], "subject": r["subject"], "status": r["status"],
                           "created_at": r["created_at"], "category": tr.get("category"),
                           "priority": tr.get("priority")})
        return {"count": count, "recent": recent}

    def attach_history(self, tickets: list[dict]) -> list[dict]:
        for t in tickets:
            t["history"] = self.customer_history(t["customer"], t["id"])
        return tickets

    def enrich(self, tickets: list[dict]) -> list[dict]:
        """Everything the review page shows next to a ticket."""
        return self.attach_history(self.attach_clusters(self.attach_reviews(tickets)))

    # -- ticket systems ----------------------------------------------------------------------------------

    def push(self, system: TicketSystem, ext_id: str, kind: str, call, *args, **kwargs):
        """Call the ticket system and log it. An outage there must never lose our triage or the human's decision."""
        try:
            res = call(ext_id, *args, **kwargs)
            mode, payload, err = res["mode"], {"requests": res["requests"]}, None
        except Exception as e:  # noqa: BLE001 - any failure is recorded in the outbox, never raised to the caller
            log.warning("%s %s failed for %s: %s", system.name, kind, ext_id, e)
            mode, payload, err = "error", {"kind": kind}, str(e)
        with self.lock:
            self.conn.execute(
                "INSERT INTO outbox (created_at, system, external_id, kind, mode, payload, error)"
                " VALUES (?,?,?,?,?,?,?)", (database.now(), system.name, ext_id, kind, mode, json.dumps(payload), err))
            self.conn.commit()
        return mode

    def _context(self, tid: int) -> dict:
        """What we know about how this ticket relates to others, for the internal note and tags."""
        row = self.fetch_one("SELECT cluster_id, duplicate_of, system, customer FROM tickets WHERE id = ?", (tid,))
        cluster = self.cluster_stats([row["cluster_id"]]).get(row["cluster_id"]) if row and row["cluster_id"] else None
        duplicate = None
        if row and row["duplicate_of"]:
            d = self.fetch_one("SELECT id, system, external_id FROM tickets WHERE id = ?", (row["duplicate_of"],))
            if d:
                same_system = d["system"] and d["system"] == row["system"]
                duplicate = f"{d['system']} ticket {d['external_id']}" if same_system else f"ticket #{d['id']}"
        return {"cluster": cluster, "duplicate_of": duplicate,
                "history": self.customer_history(row["customer"], tid) if row else {"count": 0, "recent": []}}

    def _tags(self, triage: Triage, ctx: dict) -> list[str]:
        """The tags written to the helpdesk: yours to route, filter and report on."""
        t = self.taxonomy.tag
        return [t("triaged"), t(f"cat_{self.taxonomy.category(triage.category.value)}"),
                t(f"team_{self.taxonomy.team(triage.team)}"),
                *([t("possible_duplicate")] if ctx["duplicate_of"] else []),
                *([t("spike")] if ctx["cluster"] and ctx["cluster"]["kind"] == "spike" else []),
                *([t("sentiment_negative")] if triage.sentiment is Sentiment.NEGATIVE else []),
                *([t(f"lang_{triage.language}")] if triage.language != "und" else [])]

    def _internal_note(self, s: Suggestion, ctx: dict | None = None) -> str:
        tr = s.triage
        tx = self.taxonomy
        arts = ", ".join(a["title"] for a in s.articles) or "none"
        lines = []
        if ctx and ctx["duplicate_of"]:
            lines.append(f"Possible duplicate of {ctx['duplicate_of']} (same customer and category, close in time).")
        if c := ctx and ctx["cluster"]:
            what = f'"{c["topic_title"]}"' if c["topic_title"] else "the same issue"
            lead = "POSSIBLE INCIDENT" if c["kind"] == "spike" else "Related"
            lines.append(f"{lead}: {c['size']} tickets from {c['customers']} customers about {what} in a short window.")
        if s.warnings:
            what = guardrails.explain([w.model_dump() for w in s.warnings])
            lines.append(f"Check before approving: the draft {what}.")
        lines.extend(s.notices)
        if ctx and ctx.get("history", {}).get("count"):
            h = ctx["history"]
            lines.append(f"Repeat customer: {h['count']} earlier ticket{'s' if h['count'] != 1 else ''}"
                         f" (latest: \"{h['recent'][0]['subject'][:60]}\", {h['recent'][0]['status']}).")
        related = ("\n".join(lines) + "\n") if lines else ""
        return (
            f"[AI triage - pending human review]\n"
            f"Category: {tx.category(tr.category.value)} | Priority: {tr.priority.value} | Team: {tx.team(tr.team)} "
            f"| Confidence: {tr.confidence} ({tr.source})\n"
            f"Sentiment: {tr.sentiment.value} | Language: {tr.language}\n"
            f"Why: {tr.rationale}\n{related}KB articles: {arts}\n{self._how_to_decide()}\n"
            f"Suggested reply:\n{s.draft_reply}"
        )

    def _how_to_decide(self) -> str:
        tx = self.taxonomy
        if not tx.helpdesk_decisions:
            return ""
        return (f"\nTo send the suggested reply to the customer, add the tag {tx.approve}. "
                f"To discard it and answer yourself, add the tag {tx.reject}. Nothing is sent until then.\n")

    def handle_webhook(self, system_name: str, headers, raw: bytes) -> dict:
        """One inbound webhook from a ticket system: verify, triage, queue, write an internal note back.
        Blocking (model, DB and ticket-system calls): call it from a worker thread in async code."""
        system = self.systems.get(system_name)
        if not system:
            raise CopilotError(404, f"unknown ticket system '{system_name}'")
        if len(raw) > MAX_WEBHOOK_BYTES:
            raise CopilotError(413, "webhook body too large")
        if not system.verify_webhook(headers, raw):
            raise CopilotError(401, "invalid webhook signature")
        try:
            inbound = system.parse_webhook(raw)
            t = TicketIn(subject=inbound.subject, body=inbound.body, customer=inbound.requester)
        except WebhookError as e:
            raise CopilotError(422, str(e)) from e
        except ValidationError as e:
            raise CopilotError(422, json.loads(e.json())) from e

        def existing():
            return self.fetch_one("SELECT id FROM tickets WHERE system = ? AND external_id = ?",
                                  (system.name, inbound.external_id))

        # Ticket systems retry failed deliveries: make this idempotent per (system, external ticket).
        if row := existing():
            return self._decide_from_helpdesk(system, row["id"], inbound)
        s = self.suggest(t)
        tid = self.store(t, s, system.name, inbound.external_id)
        if tid is None:  # an identical webhook finished first while we were triaging
            return {"id": existing()["id"], "duplicate": True}
        ctx = self._context(tid)
        # Internal note only: the customer sees nothing until a human approves.
        self.push(system, inbound.external_id, "triage_note", system.post_internal_note,
                  self._internal_note(s, ctx), priority=s.triage.priority, category=s.triage.category,
                  tags=self._tags(s.triage, ctx))
        return {"id": tid, "status": Status.PENDING_REVIEW, "suggestion": s}

    def _decide_from_helpdesk(self, system: TicketSystem, tid: int, inbound) -> dict:
        """A ticket we already triaged was updated. If a person tagged it to approve or reject, that is their
        decision; any other update (including our own notes coming back as webhooks) changes nothing."""
        already = {"id": tid, "duplicate": True}
        decision, conflict = self.taxonomy.decision(inbound.tags)
        if conflict:
            log.warning("%s ticket %s has both %s and %s: acting on neither", system.name, inbound.external_id,
                        self.taxonomy.approve, self.taxonomy.reject)
            return {**already, "ignored": "both decision tags are set"}
        if decision is None:
            return already
        if system.configured and not os.getenv(system.secret_env) and not self.allow_unsigned_decisions:
            log.warning("%s ticket %s: a decision by tag is ignored because %s is not set", system.name,
                        inbound.external_id, system.secret_env)
            return {**already, "ignored": f"decisions by tag need {system.secret_env} to be set"}
        actor = (inbound.actor or "").strip()
        if self.allowed_reviewers is not None and actor.lower() not in self.allowed_reviewers:
            log.warning("%s ticket %s: %r may not decide from the helpdesk", system.name, inbound.external_id, actor)
            return {**already, "ignored": "reviewer is not allowed to decide from the helpdesk"}
        try:
            result = self.review(tid, Review(action=decision, reviewer=actor or f"{system.name} agent"))
        except CopilotError as e:
            if e.status == 409:  # retried delivery, or the update caused by our own reply
                return {**already, "ignored": "already decided"}
            raise
        return {**result, "decided_in": system.name}

    # -- the human decision ------------------------------------------------------------------------------

    def review(self, ticket_id: int, r: Review) -> dict:
        """Record a human decision. For tickets from a ticket system, an approved reply goes to the customer
        as a public reply."""
        row = self.fetch_one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        if not row:
            raise CopilotError(404, "ticket not found")
        s = json.loads(row["suggestion"])
        ai = s["triage"]
        final_cat = r.final_category.value if r.final_category else ai["category"]
        final_pri = r.final_priority.value if r.final_priority else ai["priority"]
        final_reply = r.edited_reply or s["draft_reply"]
        with self.lock:
            # Atomic claim: of two simultaneous reviews exactly one changes the row, the other gets a 409.
            claimed = self.conn.execute("UPDATE tickets SET status = ? WHERE id = ? AND status = ?",
                                        (r.action.value, ticket_id, Status.PENDING_REVIEW.value)).rowcount
            if not claimed:
                self.conn.rollback()
                raise CopilotError(409, "ticket already reviewed")
            self.conn.execute(
                "INSERT INTO reviews (ticket_id, created_at, reviewer, action, final_category, final_priority,"
                " final_reply, ai_category, ai_priority, ai_reply, reasons, note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ticket_id, database.now(), r.reviewer, r.action.value, final_cat, final_pri, final_reply,
                 ai["category"], ai["priority"], s["draft_reply"], json.dumps([x.value for x in r.reasons]), r.note),
            )
            self.conn.commit()

        system = self.systems.get(row["system"]) if row["system"] else None
        sync = None
        if system:
            ext = row["external_id"]
            if r.action is Decision.APPROVED:
                sync = self.push(system, ext, "public_reply", system.post_public_reply, final_reply,
                                 priority=Priority(final_pri), category=Category(final_cat))
            else:
                sync = self.push(system, ext, "rejection_note", system.post_rejection, r.reviewer,
                                 tag=self.taxonomy.tag("rejected"))
        return {"id": ticket_id, "status": r.action, "sync": sync,
                "warnings": guardrails.find_commitments(final_reply)}

    def list_clusters(self) -> list[dict]:
        """Groups of tickets about the same issue that still have something to review: possible incidents first."""
        ids = [r["cluster_id"] for r in self.fetch_all(
            "SELECT DISTINCT cluster_id FROM tickets WHERE cluster_id IS NOT NULL AND status = 'pending_review'")]
        out = []
        for c in self.cluster_stats(ids).values():
            rows = self.fetch_all("SELECT id, status FROM tickets WHERE cluster_id = ? ORDER BY id", (c["id"],))
            out.append({**c, "ticket_ids": [r["id"] for r in rows],
                        "pending_ids": [r["id"] for r in rows if r["status"] == Status.PENDING_REVIEW.value]})
        out.sort(key=lambda c: (c["kind"] != "spike", -c["size"], c["id"]))
        return out

    def review_cluster(self, cluster_id: int, r: Review) -> dict:
        """One decision for every pending ticket of a possible incident. Without `edited_reply` each ticket keeps
        its own draft; with it, the same text goes to all of them. Each ticket is recorded as its own review."""
        c = self.cluster_stats([cluster_id]).get(cluster_id)
        if not c:
            raise CopilotError(404, "cluster not found")
        if c["kind"] != "spike":
            raise CopilotError(409, "bulk review is only for possible incidents (several different customers)")
        pending = [t["id"] for t in self.fetch_all(
            "SELECT id FROM tickets WHERE cluster_id = ? AND status = ? ORDER BY id",
            (cluster_id, Status.PENDING_REVIEW.value))]
        if not pending:
            raise CopilotError(409, "nothing pending in this cluster")
        reviewed, skipped, results = [], [], []
        for tid in pending:
            try:
                results.append(self.review(tid, r))
                reviewed.append(tid)
            except CopilotError as e:
                if e.status != 409:  # someone reviewed it a moment ago: skip it, keep going
                    raise
                skipped.append(tid)
        return {"cluster": cluster_id, "reviewed": reviewed, "skipped": skipped, "results": results}

    # -- how the AI is doing -----------------------------------------------------------------------------

    def metrics(self) -> dict:
        """How often humans agree with the AI: the number that decides whether to automate more."""
        rows = self.fetch_all("SELECT * FROM reviews WHERE action = 'approved'")
        n = len(rows)
        if not n:
            return {"reviewed": 0}
        return {
            "reviewed": n,
            "category_agreement": round(sum(r["ai_category"] == r["final_category"] for r in rows) / n, 2),
            "priority_agreement": round(sum(r["ai_priority"] == r["final_priority"] for r in rows) / n, 2),
            "reply_edit_rate": round(sum(r["ai_reply"] != r["final_reply"] for r in rows) / n, 2),
        }

    def metrics_breakdown(self) -> dict:
        """Quality of the AI per category, why humans corrected it, and where it may have earned more autonomy."""
        return feedback.breakdown(self.fetch_all(
            "SELECT action, ai_category, ai_priority, ai_reply, final_category, final_priority, final_reply, reasons"
            " FROM reviews"))

    def outbox(self, limit: int = 50) -> list[dict]:
        """Audit log of calls to ticket systems (sent / dry_run / error)."""
        rows = self.fetch_all("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]
