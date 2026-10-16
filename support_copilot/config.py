"""How support-copilot speaks your helpdesk's language: the tags it writes, the names it uses for categories and
teams, and which tags a person adds to approve or reject the AI's reply from inside the ticket.

Tags are the interface on purpose: your helpdesk's own triggers and views can route on them (for example "tag
ai_team_finance: assign to the Finance group") without support-copilot knowing your groups, ids or custom fields.
"""
import re
from dataclasses import dataclass, field

from .models import Decision

_LABEL = re.compile(r"^[a-z0-9][a-z0-9_-]*$")  # what helpdesks accept in a tag: no spaces, no capitals


def _check(kind: str, value: str) -> None:
    if not _LABEL.match(value):
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
        for label in (*self.category_labels.values(), *self.team_labels.values()):
            _check("label", label)

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
