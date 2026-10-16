"""How support-copilot speaks your helpdesk's language: the tags it writes, the names it uses for categories and
teams, and which tags a person adds to approve or reject the AI's reply from inside the ticket.

Tags are the interface on purpose: your helpdesk's own triggers and views can route on them (for example "tag
ai_team_finance: assign to the Finance group") without support-copilot knowing your groups, ids or custom fields.
"""
import os
import re
from dataclasses import dataclass, field

from .models import Category, Decision

_LABEL = re.compile(r"[a-z0-9][a-z0-9_-]*")  # what helpdesks accept in a tag: no spaces, no capitals


def _check(kind: str, value: str) -> None:
    if not _LABEL.fullmatch(value):
        raise ValueError(f"{kind} {value!r} must be lowercase letters, digits, '_' or '-' (a valid helpdesk tag)")


@dataclass(frozen=True)
class Taxonomy:
    """
    tag_prefix        goes in front of every tag we write (default "ai_": ai_triaged, ai_cat_billing, ...)
    category_labels   our category -> your name for it, e.g. {"billing": "finance-queries"}; two categories may
                      share a label. Categories not listed keep our name.
    team_labels       our team -> your name for it, e.g. {"finance": "accounts-receivable"}
    approve_tag       the person adds `<prefix><approve_tag>` (ai_approve) to send the suggested reply
    reject_tag        the person adds `<prefix><reject_tag>` (ai_reject) to discard it and handle the ticket by hand
    helpdesk_decisions  False turns off deciding from inside the helpdesk: only the API and review page can approve
    """

    tag_prefix: str = "ai_"
    category_labels: dict[str, str] = field(default_factory=dict)
    team_labels: dict[str, str] = field(default_factory=dict)
    approve_tag: str = "approve"
    reject_tag: str = "reject"
    helpdesk_decisions: bool = True

    def __post_init__(self):
        _check("tag_prefix", self.tag_prefix.rstrip("_-") or "x")
        for name in (self.approve_tag, self.reject_tag):
            _check("decision tag", name)
        if self.approve_tag == self.reject_tag:
            raise ValueError("approve_tag and reject_tag must differ")
        from .classifier import TEAMS
        for given, known, what in ((self.category_labels, [c.value for c in Category], "category"),
                                   (self.team_labels, sorted(set(TEAMS.values())), "team")):
            if unknown := sorted(set(given) - set(known)):
                raise ValueError(f"unknown {what} {unknown[0]!r} in the {what} labels: use one of {', '.join(known)}")
        for label in (*self.category_labels.values(), *self.team_labels.values()):
            _check("label", label)

    @classmethod
    def from_env(cls) -> "Taxonomy":
        """The same settings from environment variables, for the reference server (see docs/api.md):
        TAG_PREFIX, APPROVE_TAG, REJECT_TAG, HELPDESK_DECISIONS (0 turns it off), CATEGORY_LABELS and TEAM_LABELS
        (like "billing=finance-queries,bug=defects")."""
        defaults = cls()
        return cls(
            tag_prefix=os.getenv("TAG_PREFIX", defaults.tag_prefix),
            category_labels=parse_pairs(os.getenv("CATEGORY_LABELS", ""), "CATEGORY_LABELS"),
            team_labels=parse_pairs(os.getenv("TEAM_LABELS", ""), "TEAM_LABELS"),
            approve_tag=os.getenv("APPROVE_TAG", defaults.approve_tag),
            reject_tag=os.getenv("REJECT_TAG", defaults.reject_tag),
            helpdesk_decisions=env_flag("HELPDESK_DECISIONS", True),
        )

    def tag(self, name: str) -> str:
        return f"{self.tag_prefix}{name}"

    def category(self, value: str) -> str:
        return self.category_labels.get(value, value)

    def team(self, value: str) -> str:
        return self.team_labels.get(value, value)

    @property
    def approve(self) -> str:
        return self.tag(self.approve_tag)

    @property
    def reject(self) -> str:
        return self.tag(self.reject_tag)

    def decision(self, tags) -> tuple[Decision | None, bool]:
        """The decision the tags ask for, and whether they contradict themselves (both tags: act on neither)."""
        if not self.helpdesk_decisions:
            return None, False
        have = {t.strip().lower() for t in tags}
        approve, reject = self.approve in have, self.reject in have
        if approve and reject:
            return None, True
        return (Decision.APPROVED if approve else Decision.REJECTED if reject else None), False


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    if value.strip().lower() in ("1", "true", "yes", "on"):
        return True
    if value.strip().lower() in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name}={value!r}: use 1 or 0")


def parse_pairs(text: str, name: str) -> dict[str, str]:
    """"billing=finance-queries,bug=defects" as a dict. A malformed entry is an error, not silently skipped."""
    out = {}
    for item in filter(None, (part.strip() for part in text.split(","))):
        key, sep, value = item.partition("=")
        if not sep or not key.strip() or not value.strip():
            raise ValueError(f"{name}: {item!r} is not name=label (example: billing=finance-queries)")
        out[key.strip()] = value.strip()
    return out
