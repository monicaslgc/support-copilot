"""Duplicate and incident-spike detection.

Two separate ideas, both rule-based so a reviewer can always see *why* tickets were linked:

- A topic cluster joins tickets about the same issue in a short window: same top knowledge-base article
  and category, or near-identical text. When several different customers land in one cluster it is
  flagged as a possible incident ("spike").
- A possible duplicate is the same customer opening another ticket of the same category in the window.
  It is only a flag: a person decides whether it really is a duplicate.

These are pure functions over `Item`s so they are easy to test; copilot.py feeds them rows from the database.
"""
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

UNKNOWN = "unknown"


def env_int(name: str, default: int, minimum: int = 1) -> int:
    """A whole number from the environment, or a message that names the variable (not "invalid literal")."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} must be a whole number") from None
    if value < minimum:
        raise ValueError(f"{name}={raw!r} must be at least {minimum}")
    return value


def window() -> timedelta:
    return timedelta(minutes=env_int("CLUSTER_WINDOW_MIN", 60))


def spike_min_customers() -> int:
    return env_int("SPIKE_MIN_CUSTOMERS", 3)


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


def normalize_customer(name: str) -> str:
    """The key two spellings of one customer share: case, surrounding and repeated spaces do not matter
    ("Joana", "joana " and "JOANA" are one customer). It is stored with every ticket and is what the database
    compares, so counting customers, history and duplicates all agree with each other."""
    return " ".join((name or "").casefold().split())


def same_customer(a: str, b: str) -> bool:
    a, b = normalize_customer(a), normalize_customer(b)
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
