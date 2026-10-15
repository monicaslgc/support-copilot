from enum import Enum
from pydantic import BaseModel, Field


class Priority(str, Enum):
    P1 = "P1"  # critical: outage, data loss, security
    P2 = "P2"  # high: major feature broken, no workaround
    P3 = "P3"  # medium: partial issue, workaround exists
    P4 = "P4"  # low: question, how-to, feature request


class Category(str, Enum):
    BUG = "bug"
    BILLING = "billing"
    ACCESS = "access"
    HOWTO = "how_to"
    FEATURE_REQUEST = "feature_request"
    OTHER = "other"


class Status(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


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


class Suggestion(BaseModel):
    triage: Triage
    articles: list[dict]
    draft_reply: str


class Review(BaseModel):
    action: Status  # approved | rejected
    reviewer: str
    edited_reply: str | None = None
    final_priority: Priority | None = None
    final_category: Category | None = None
