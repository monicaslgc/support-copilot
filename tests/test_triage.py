import os

os.environ.pop("ANTHROPIC_API_KEY", None)  # tests run offline, rules mode
os.environ["TRIAGE_DB"] = ":memory:"

import pytest
from fastapi.testclient import TestClient

from support_copilot.classifier import classify_rules
from support_copilot.kb import KnowledgeBase
from support_copilot.main import app
from support_copilot.models import Category, Priority


@pytest.mark.parametrize(
    "subject,body,cat,prio",
    [
        ("Platform is down", "total outage since 9am", Category.BUG, Priority.P1),
        ("Duplicate charge", "I want a refund for this invoice", Category.BILLING, Priority.P3),
        ("Login", "I forgot my password", Category.ACCESS, Priority.P3),
        ("Export", "how do I export a report?", Category.HOWTO, Priority.P4),
        ("Idea", "it would be nice to add dark mode", Category.FEATURE_REQUEST, Priority.P4),
        ("Save fails", "error and crash when saving", Category.BUG, Priority.P2),
    ],
)
def test_rules(subject, body, cat, prio):
    t = classify_rules(subject, body)
    assert (t.category, t.priority) == (cat, prio)


def test_unmatched_ticket_has_low_confidence():
    assert classify_rules("hello", "just saying hi").confidence < 0.5


def test_kb_retrieval():
    kb = KnowledgeBase()
    assert kb.search("forgot my password cannot log in")[0]["id"] == "reset-password"
    assert kb.search("xyzzy plugh") == []
    assert kb.search("it would be nice to add dark mode") == []


def test_kb_drops_weak_matches_next_to_a_clear_hit():
    kb = KnowledgeBase()
    outage = kb.search("Platform is down. Nobody on my team can access anything, total outage since 9am.")
    assert [a["id"] for a in outage] == ["service-outage"]
    export_q = "How to export a report? How do I export the monthly report to CSV?"
    assert [a["id"] for a in kb.search(export_q)] == ["export-report"]
    # but a genuinely mixed question still returns both articles
    both = kb.search("forgot my password and also need the invoice for the refund")
    assert {"reset-password", "invoices"} <= {a["id"] for a in both}
    assert len(kb.search("password invoice refund", relative=0)) >= len(kb.search("password invoice refund"))


def test_kb_matches_singular_and_plural_forms():
    kb = KnowledgeBase()
    assert kb.search("my invoice and the refund")[0]["id"] == "invoices"      # article says invoices / refunds
    assert kb.search("user permission")[0]["id"] == "user-permissions"


def test_full_flow_and_metrics():
    c = TestClient(app)
    r = c.post("/tickets", json={"subject": "Duplicate charge", "body": "refund my invoice please", "customer": "Sam"})
    assert r.status_code == 201
    tid = r.json()["id"]
    assert r.json()["status"] == "pending_review"
    assert r.json()["suggestion"]["articles"][0]["id"] == "invoices"

    # human fixes the priority and edits the reply
    ok = c.post(f"/tickets/{tid}/review", json={
        "action": "approved", "reviewer": "monica",
        "final_priority": "P2", "edited_reply": "Hi Sam, refund approved.",
    })
    assert ok.status_code == 200
    assert c.post(f"/tickets/{tid}/review", json={"action": "approved", "reviewer": "x"}).status_code == 409

    m = c.get("/metrics").json()
    assert m["reviewed"] == 1 and m["category_agreement"] == 1.0
    assert m["priority_agreement"] == 0.0 and m["reply_edit_rate"] == 1.0


def test_queue_is_sorted_by_priority():
    c = TestClient(app)
    c.post("/tickets", json={"subject": "Idea", "body": "it would be nice to add dark mode"})
    c.post("/tickets", json={"subject": "Outage", "body": "everything is down, outage"})
    pending = c.get("/tickets", params={"status": "pending_review"}).json()
    prios = [t["suggestion"]["triage"]["priority"] for t in pending]
    assert prios == sorted(prios)


def test_validation_and_404():
    c = TestClient(app)
    assert c.post("/tickets", json={"subject": "x", "body": "y"}).status_code == 422
    assert c.get("/tickets/9999").status_code == 404
    assert c.post("/tickets/9999/review", json={"action": "approved", "reviewer": "x"}).status_code == 404


# ---------------- "down" and "security" are everyday words: only an outage or a breach is P1
import pytest  # noqa: E402


@pytest.mark.parametrize("subject,body", [
    ("How do I scroll down the page?", "how do I scroll down to the totals?"),
    ("Update security questions", "How do I change my security questions in settings?"),
    ("Slow down", "Can you slow down the export, it is freezing"),
    ("Countdown timer", "The countdown timer shows the wrong date"),
])
def test_everyday_uses_of_down_and_security_are_not_an_outage(subject, body):
    assert classify_rules(subject, body).priority is not Priority.P1


@pytest.mark.parametrize("subject,body", [
    ("Platform is down", "nobody can log in"),
    ("Dashboard down", "The dashboard has been down since 9am"),
    ("Is it down?", "Is it down for everyone or only me?"),
    ("Everything is down", "everything is down for everyone"),
    ("Our site is totally down", "customers cannot order"),
    ("Security breach", "We found a security breach in our account"),
    ("Hacked", "I think my account was hacked"),
])
def test_real_outages_and_breaches_are_still_p1(subject, body):
    assert classify_rules(subject, body).priority is Priority.P1
