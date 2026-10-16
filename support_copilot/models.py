from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Priority(StrEnum):
    P1 = "P1"  # critical: outage, data loss, security
    P2 = "P2"  # high: major feature broken, no workaround
    P3 = "P3"  # medium: partial issue, workaround exists
    P4 = "P4"  # low: question, how-to, feature request


class Category(StrEnum):
    BUG = "bug"
    BILLING = "billing"
    ACCESS = "access"
    HOWTO = "how_to"
    FEATURE_REQUEST = "feature_request"
    OTHER = "other"


class Status(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class Sentiment(StrEnum):
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    POSITIVE = "positive"


class Decision(StrEnum):
    """What a human can decide. Deliberately has no 'pending' value."""

    APPROVED = "approved"
    REJECTED = "rejected"


class Reason(StrEnum):
    """Why a reviewer corrected or rejected the AI. Optional, but it is the feedback that improves it."""

    WRONG_CATEGORY = "wrong_category"
    WRONG_PRIORITY = "wrong_priority"
    TONE = "tone"
    MISSING_INFO = "missing_info"
    INACCURATE = "inaccurate"
    DUPLICATE = "duplicate"
    OTHER = "other"


class TicketIn(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=3, max_length=10_000)
    customer: str = "unknown"


class Triage(BaseModel):
    category: Category
    priority: Priority
    team: str
    confidence: float = Field(ge=0, le=1)
    rationale: str
    source: str  # "llm" or "rules"
    sentiment: Sentiment = Sentiment.NEUTRAL
    language: str = "und"  # ISO 639-1 code, or "und" when unclear


class Flag(BaseModel):
    """A promise found in a reply: kind (refund, guarantee, benefit, money, deadline) and how serious."""

    kind: str
    severity: str  # "hard": a person must stand behind it; "soft": may be copied correctly from the KB
    text: str


class Suggestion(BaseModel):
    triage: Triage
    articles: list[dict]
    draft_reply: str
    warnings: list[Flag] = Field(default_factory=list)  # promises found in the draft
    notices: list[str] = Field(default_factory=list)  # what the guardrails did, for the reviewer


class Review(BaseModel):
    action: Decision
    reviewer: str = Field(min_length=1, max_length=200)
    edited_reply: str | None = Field(default=None, max_length=10_000)
    final_priority: Priority | None = None
    final_category: Category | None = None
    reasons: list[Reason] = Field(default_factory=list, max_length=len(Reason))
    note: str | None = Field(default=None, max_length=500)

    @field_validator("edited_reply")
    @classmethod
    def _not_blank(cls, v: str | None) -> str | None:
        """An empty reply is never what a person meant to send: it would otherwise fall back to the AI's draft."""
        if v is not None and not v.strip():
            raise ValueError("the reply is empty: leave edited_reply out to send the draft")
        return v

    @field_validator("reviewer")
    @classmethod
    def _named(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("the reviewer is the person who decided: a name or an email")
        return v.strip()

    @field_validator("reasons")
    @classmethod
    def _no_repeats(cls, v: list[Reason]) -> list[Reason]:
        return list(dict.fromkeys(v))


class ClusterReview(Review):
    """One decision for several tickets of an incident: `ticket_ids` are the tickets the reviewer was shown."""

    ticket_ids: list[int] = Field(min_length=1, max_length=500)
