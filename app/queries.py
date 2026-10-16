"""Read side: what a ticket needs next to it when it is shown (its review, its group, the customer's history)."""
import json

from . import clusters, state


def attach_reviews(tickets: list[dict]) -> list[dict]:
    """Add `review` (what the human decided and the reply that was actually approved) to each ticket."""
    ids = [t["id"] for t in tickets]
    by_ticket = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in state.fetch_all(
                f"SELECT ticket_id, reviewer, action, final_category, final_priority, final_reply, created_at,"
                f" reasons, note FROM reviews WHERE ticket_id IN ({marks})", tuple(ids)):
            by_ticket[r["ticket_id"]] = {k: r[k] for k in r.keys() if k != "ticket_id"}
            by_ticket[r["ticket_id"]]["reasons"] = json.loads(r["reasons"] or "[]")
    for t in tickets:
        t["review"] = by_ticket.get(t["id"])
    return tickets


def cluster_stats(ids) -> dict[int, dict]:
    """Size, distinct customers and kind for each cluster of 2+ tickets. kind is "spike" (a possible
    incident) when enough different customers are affected, else "related"."""
    ids = [i for i in set(ids) if i is not None]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = state.fetch_all(
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
            "topic": r["topic"], "topic_title": state.kb.title(r["topic"]),
            "first_seen": r["first_seen"], "last_seen": r["last_seen"], "pending": r["pending"] or 0,
        }
    return out


def attach_clusters(tickets: list[dict]) -> list[dict]:
    """Add `cluster` (null, or the stats of the group of tickets about the same issue) to each ticket."""
    stats = cluster_stats([t.get("cluster_id") for t in tickets])
    for t in tickets:
        t["cluster"] = stats.get(t.get("cluster_id"))
    return tickets


def customer_history(customer: str, before_id: int, limit: int = 5) -> dict:
    """The same customer's earlier tickets (by the customer string the ticket system sent: a name or an
    email, so two people with the same name are indistinguishable). "unknown" has no history."""
    if not customer.strip() or customer.strip().lower() == clusters.UNKNOWN:
        return {"count": 0, "recent": []}
    where, params = "lower(customer) = lower(?) AND id < ?", (customer.strip(), before_id)
    count = state.fetch_one(f"SELECT COUNT(*) AS n FROM tickets WHERE {where}", params)["n"]
    recent = []
    for r in state.fetch_all(f"SELECT id, subject, status, created_at, suggestion FROM tickets WHERE {where}"
                             " ORDER BY id DESC LIMIT ?", (*params, limit)):
        tr = json.loads(r["suggestion"]).get("triage", {})
        recent.append({"id": r["id"], "subject": r["subject"], "status": r["status"], "created_at": r["created_at"],
                       "category": tr.get("category"), "priority": tr.get("priority")})
    return {"count": count, "recent": recent}


def attach_history(tickets: list[dict]) -> list[dict]:
    for t in tickets:
        t["history"] = customer_history(t["customer"], t["id"])
    return tickets


def enrich(tickets: list[dict]) -> list[dict]:
    """Everything the review page shows next to a ticket."""
    return attach_history(attach_clusters(attach_reviews(tickets)))
