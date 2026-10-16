import json

from fastapi import APIRouter, Depends, Query

from .. import feedback, state
from ..security import require_api_key

router = APIRouter(tags=["metrics"], dependencies=[Depends(require_api_key)])


@router.get("/metrics")
def metrics():
    """How often humans agree with the AI: the number that decides whether to automate more."""
    rows = state.fetch_all("SELECT * FROM reviews WHERE action = 'approved'")
    n = len(rows)
    if not n:
        return {"reviewed": 0}
    return {
        "reviewed": n,
        "category_agreement": round(sum(r["ai_category"] == r["final_category"] for r in rows) / n, 2),
        "priority_agreement": round(sum(r["ai_priority"] == r["final_priority"] for r in rows) / n, 2),
        "reply_edit_rate": round(sum(r["ai_reply"] != r["final_reply"] for r in rows) / n, 2),
    }


@router.get("/metrics/breakdown")
def metrics_breakdown():
    """Quality of the AI per category, why humans corrected it, and where it may have earned more autonomy."""
    return feedback.breakdown(state.fetch_all(
        "SELECT action, ai_category, ai_priority, ai_reply, final_category, final_priority, final_reply, reasons"
        " FROM reviews"))


@router.get("/outbox")
def outbox(limit: int = Query(50, ge=1, le=500)):
    """Audit log of calls to ticket systems (sent / dry_run / error)."""
    rows = state.fetch_all("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]
