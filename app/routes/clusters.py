from fastapi import APIRouter, Depends, HTTPException

from .. import queries, reviews, state
from ..models import Review, Status
from ..security import require_api_key

router = APIRouter(tags=["incidents"], dependencies=[Depends(require_api_key)])


@router.get("/clusters")
def list_clusters():
    """Groups of tickets about the same issue that still have something to review: possible incidents first."""
    ids = [r["cluster_id"] for r in state.fetch_all(
        "SELECT DISTINCT cluster_id FROM tickets WHERE cluster_id IS NOT NULL AND status = 'pending_review'")]
    out = []
    for c in queries.cluster_stats(ids).values():
        rows = state.fetch_all("SELECT id, status FROM tickets WHERE cluster_id = ? ORDER BY id", (c["id"],))
        out.append({**c, "ticket_ids": [r["id"] for r in rows],
                    "pending_ids": [r["id"] for r in rows if r["status"] == Status.PENDING_REVIEW.value]})
    out.sort(key=lambda c: (c["kind"] != "spike", -c["size"], c["id"]))
    return out


@router.post("/clusters/{cluster_id}/review")
def review_cluster(cluster_id: int, r: Review):
    """One decision for every pending ticket of a possible incident. Without `edited_reply` each ticket keeps
    its own draft; with it, the same text goes to all of them. Each ticket is recorded as its own review."""
    c = queries.cluster_stats([cluster_id]).get(cluster_id)
    if not c:
        raise HTTPException(404, "cluster not found")
    if c["kind"] != "spike":
        raise HTTPException(409, "bulk review is only for possible incidents (several different customers)")
    pending = [t["id"] for t in state.fetch_all(
        "SELECT id FROM tickets WHERE cluster_id = ? AND status = ? ORDER BY id",
        (cluster_id, Status.PENDING_REVIEW.value))]
    if not pending:
        raise HTTPException(409, "nothing pending in this cluster")
    reviewed, skipped, results = [], [], []
    for tid in pending:
        try:
            results.append(reviews.apply_review(tid, r))
            reviewed.append(tid)
        except HTTPException as e:
            if e.status_code != 409:  # someone reviewed it a moment ago: skip it, keep going
                raise
            skipped.append(tid)
    return {"cluster": cluster_id, "reviewed": reviewed, "skipped": skipped, "results": results}
