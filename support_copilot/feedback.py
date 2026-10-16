"""What the reviews say about the AI, per category.

The question this answers: where has the AI earned more autonomy? A raw "100% agreement" over 3 reviews
proves nothing, so the headline number is the lower bound of a Wilson score interval, which stays low until
there are enough reviews. This module only reports; nothing is ever automated from it.
"""
import json
import math
import os
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


def wilson_lower(successes: int, n: int, z: float = 1.96) -> float:
    """Lower bound of the 95% Wilson score interval for a rate: a conservative estimate of the true rate."""
    if n == 0:
        return 0.0
    p = successes / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (centre - margin) / (1 + z * z / n)


def min_reviews() -> int:
    return int(os.getenv("AUTOMATION_MIN_REVIEWS", "20"))


def min_lower_bound() -> float:
    return float(os.getenv("AUTOMATION_MIN_LOWER_BOUND", "0.9"))


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 2) if whole else None


def breakdown(rows: Iterable[Mapping]) -> dict:
    """Per-category quality of the AI's triage and drafts, and why humans corrected it.

    A review is *clean* when it was approved with the category, the priority and the reply all unchanged.
    An *override* is anything else: a rejection, or an approval that changed one of the three.
    """
    cats: dict[str, Counter] = {}
    reasons: Counter = Counter()
    total: Counter = Counter()
    for r in rows:
        c = cats.setdefault(r["ai_category"], Counter())
        approved = r["action"] == "approved"
        same_cat = r["ai_category"] == r["final_category"]
        same_pri = r["ai_priority"] == r["final_priority"]
        same_reply = r["ai_reply"] == r["final_reply"]
        clean = approved and same_cat and same_pri and same_reply
        override = not clean
        given = json.loads(r["reasons"] or "[]")
        for key, hit in (("reviewed", True), ("approved", approved), ("rejected", not approved), ("clean", clean),
                         ("cat_same", approved and same_cat), ("pri_same", approved and same_pri),
                         ("reply_edited", approved and not same_reply)):
            c[key] += hit
            total[key] += hit
        total["overrides"] += override
        total["overrides_with_reason"] += override and bool(given)
        reasons.update(given)

    by_category: list[dict[str, Any]] = []
    for name, c in cats.items():
        lower = wilson_lower(c["clean"], c["reviewed"])
        by_category.append({
            "category": name, "reviewed": c["reviewed"], "approved": c["approved"], "rejected": c["rejected"],
            "rejection_rate": _rate(c["rejected"], c["reviewed"]),
            "category_agreement": _rate(c["cat_same"], c["approved"]),
            "priority_agreement": _rate(c["pri_same"], c["approved"]),
            "reply_edit_rate": _rate(c["reply_edited"], c["approved"]),
            "clean_rate": _rate(c["clean"], c["reviewed"]),
            "clean_rate_lower_bound": round(lower, 2),
            "automation_candidate": c["reviewed"] >= min_reviews() and lower >= min_lower_bound(),
        })
    by_category.sort(key=lambda x: (-x["reviewed"], x["category"]))
    return {
        "reviewed": total["reviewed"], "rejected": total["rejected"], "overrides": total["overrides"],
        "reason_coverage": _rate(total["overrides_with_reason"], total["overrides"]),
        "reasons": dict(reasons.most_common()),
        "by_category": by_category,
        "automation": {"min_reviews": min_reviews(), "min_lower_bound": min_lower_bound(),
                       "note": "Informational only: nothing is automated."},
    }
