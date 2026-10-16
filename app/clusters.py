"""Duplicate and incident-spike detection.

Two separate ideas, both rule-based so a reviewer can always see *why* tickets were linked:

- A topic cluster joins tickets about the same issue in a short window: same top knowledge-base article
  and category, or near-identical text. When several different customers land in one cluster it is
  flagged as a possible incident ("spike").
- A possible duplicate is the same customer opening another ticket of the same category in the window.
  It is only a flag: a person decides whether it really is a duplicate.

These are pure functions over `Item`s so they are easy to test; main.py feeds them rows from the database.
"""
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

UNKNOWN = "unknown"


def window() -> timedelta:
    return timedelta(minutes=int(os.getenv("CLUSTER_WINDOW_MIN", "60")))


def spike_min_customers() -> int:
    return int(os.getenv("SPIKE_MIN_CUSTOMERS", "3"))


@dataclass(frozen=True)
class Item:
    id: int
    customer: str
    category: str
    topic: str | None  # id of the best matching KB article, if any
    tokens: frozenset[str]
    created_at: datetime
    cluster_id: int | None = None


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def same_customer(a: str, b: str) -> bool:
    a, b = a.strip().casefold(), b.strip().casefold()
    return bool(a) and a != UNKNOWN and a == b  # "unknown" is the absence of a customer, never a match


def _in_window(new: Item, other: Item, within: timedelta) -> bool:
    return abs(new.created_at - other.created_at) <= within


def find_cluster(new: Item, others: Iterable[Item], *, within: timedelta | None = None,
                 min_similarity: float = 0.5) -> Item | None:
    """The earliest ticket `new` belongs with, or None. Its cluster (or its own id) becomes the cluster id."""
    within = within or window()
    related = [
        o for o in others
        if _in_window(new, o, within)
        and ((new.topic is not None and new.topic == o.topic and new.category == o.category)
             or jaccard(new.tokens, o.tokens) >= min_similarity)
    ]
    return min(related, key=lambda o: (o.created_at, o.id), default=None)


def find_duplicate(new: Item, others: Iterable[Item], *, within: timedelta | None = None) -> Item | None:
    """The earliest ticket from the same customer in the same category within the window, or None."""
    within = within or window()
    same = [o for o in others
            if _in_window(new, o, within) and same_customer(new.customer, o.customer) and new.category == o.category]
    return min(same, key=lambda o: (o.created_at, o.id), default=None)
