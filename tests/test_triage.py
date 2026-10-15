import os

os.environ.pop("ANTHROPIC_API_KEY", None)  # tests run offline, rules mode
os.environ["TRIAGE_DB"] = ":memory:"

import pytest
from fastapi.testclient import TestClient

from app.classifier import classify_rules
from app.kb import KnowledgeBase
from app.main import app
from app.models import Category, Priority


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
