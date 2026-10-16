"""The queue stays fast as it grows, old databases are upgraded, and open deployments are called out."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from support_copilot import copilot as core
from support_copilot import main
from support_copilot.db import connect
from support_copilot.models import Suggestion, TicketIn, Triage
from tests.fakes import FakeZendesk

OUTAGE = ("Platform is down", "Nobody on my team can access anything, total outage since 9am.")


def make(c, subject, body, customer="unknown"):
    return c.post("/tickets", json={"subject": subject, "body": body, "customer": customer}).json()["id"]


# ---------------- the queue ----------------
def test_the_queue_is_sorted_and_limited_by_urgency_in_the_database():
    c = TestClient(main.app)
    make(c, "How to export a report?", "how do I export the monthly report to CSV")       # P4, oldest
    make(c, "Duplicate charge", "refund my invoice please")                               # P3
    make(c, "Save fails", "error and crash when saving")                                  # P2
    make(c, *OUTAGE)                                                                      # P1, newest
    top = c.get("/tickets", params={"limit": 2}).json()
    assert [t["suggestion"]["triage"]["priority"] for t in top] == ["P1", "P2"]
    assert [t["suggestion"]["triage"]["priority"] for t in c.get("/tickets").json()] == ["P1", "P2", "P3", "P4"]
    assert c.get("/tickets", params={"status": "approved"}).json() == []


def test_equal_priority_tickets_keep_arrival_order():
    c = TestClient(main.app)
    ids = [make(c, f"Duplicate charge {i}", "refund my invoice please") for i in range(4)]
    assert [t["id"] for t in c.get("/tickets").json()] == ids


# ---------------- linking at scale ----------------
def test_the_earliest_same_topic_ticket_is_found_however_far_back_it_is():
    c = TestClient(main.app)
    first = make(c, *OUTAGE, customer="Ana")
    for i in range(230):                                  # far more tickets than the text comparison looks at
        make(c, f"Feature request {i}", "it would be nice to add dark mode")
    last = make(c, "Cannot reach the platform", "The platform is unavailable, is it down?", customer="Rui")
    assert c.get(f"/tickets/{last}").json()["cluster"]["id"] == first


def test_near_identical_text_is_only_searched_among_the_most_recent_tickets(monkeypatch):
    monkeypatch.setattr(core, "SIMILARITY_SCAN", 3)
    c = TestClient(main.app)
    text = ("Totally unrelated words about turquoise elephants dancing",
            "turquoise elephants dancing everywhere tonight")
    old = make(c, *text, customer="Ana")
    for i in range(5):
        make(c, f"Feature request {i}", "it would be nice to add dark mode")
    assert c.get(f"/tickets/{make(c, *text, customer='Rui')}").json()["cluster"] is None   # beyond the last 3
    recent = make(c, *text, customer="Sam")
    assert c.get(f"/tickets/{recent}").json()["cluster"]["id"] != old                      # joined a later one


def test_the_lookups_use_indexes_instead_of_scanning_the_table():
    c = TestClient(main.app)
    for i in range(20):
        make(c, *OUTAGE, customer=f"c{i}")
    seen = []
    main.copilot.conn.set_trace_callback(seen.append)
    make(c, *OUTAGE, customer="newcomer")
    main.copilot.conn.set_trace_callback(None)
    by_topic = [q for q in seen if q.lstrip().startswith("SELECT") and "topic =" in q]
    by_customer = [q for q in seen if q.lstrip().startswith("SELECT") and "lower(customer) =" in q]
    assert len(by_topic) == len(by_customer) == 1              # one row each, found through its own index
    for sql, index in ((by_topic[0], "ix_tickets_topic"), (by_customer[0], "ix_tickets_customer")):
        plan = " ".join(str(r[3]) for r in main.copilot.conn.execute("EXPLAIN QUERY PLAN " + sql))
        assert index in plan, plan
        assert "SCAN tickets" not in plan, plan


# ---------------- storage ----------------
def test_a_real_data_error_is_raised_not_mistaken_for_a_repeated_webhook():
    s = Suggestion(triage=Triage(category="bug", priority="P2", team="x", confidence=0.5, rationale="r",
                                 source="rules"), articles=[], draft_reply="Hi")
    broken = TicketIn.model_construct(subject=None, body="Body", customer="Ana")       # NOT NULL column
    with pytest.raises(sqlite3.IntegrityError):
        main.copilot.store(broken, s)


def test_the_unique_key_still_deduplicates_quietly():
    c = TestClient(main.app)
    fz = FakeZendesk()
    fz.seed(5)
    main.copilot.systems["zendesk"] = fz.make()
    a = c.post("/webhooks/zendesk", json=fz.payload(5, *OUTAGE, "Ana")).json()
    assert c.post("/webhooks/zendesk", json=fz.payload(5, *OUTAGE, "Ana")).json() == {"id": a["id"], "duplicate": True}


# ---------------- older databases ----------------
def old_database(path):
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
            subject TEXT NOT NULL, body TEXT NOT NULL, customer TEXT NOT NULL, suggestion TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_review', system TEXT, external_id TEXT,
            UNIQUE (system, external_id));
    """)
    triage = {"category": "billing", "priority": "P2", "team": "finance", "confidence": 0.6, "rationale": "r",
              "source": "rules"}
    rows = [("a", json.dumps({"triage": triage, "articles": [], "draft_reply": "Hi"})),
            ("b", "{}"), ("c", "not json at all")]
    for subject, suggestion in rows:
        old.execute("INSERT INTO tickets (created_at, subject, body, customer, suggestion) VALUES (?,?,?,?,?)",
                    ("2026-10-15T09:00:00+00:00", subject, "body", "Ana", suggestion))
    old.commit()
    old.close()


def test_category_and_priority_are_filled_in_for_tickets_from_an_older_database(tmp_path):
    path = str(tmp_path / "old.db")
    old_database(path)
    conn = connect(path)
    got = [tuple(r) for r in conn.execute("SELECT subject, category, priority FROM tickets ORDER BY id")]
    assert got == [("a", "billing", "P2"), ("b", "other", "P3"), ("c", "other", "P3")]    # unreadable rows: defaults
    assert connect(path) is not None                                                         # safe to run twice
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"ix_tickets_topic", "ix_tickets_customer", "ix_tickets_queue", "ix_tickets_cluster"} <= names


# ---------------- an open deployment is called out ----------------
def connected(monkeypatch):
    fz = FakeZendesk()
    main.copilot.systems["zendesk"] = fz.make()                        # has credentials, so it is "connected"


def test_nothing_to_warn_about_before_a_ticket_system_is_connected():
    assert main.copilot.security_warnings() == []


def test_a_connected_system_without_secrets_is_called_out(monkeypatch):
    connected(monkeypatch)
    warnings = main.copilot.security_warnings()
    assert len(warnings) == 2
    assert "ZENDESK_WEBHOOK_SECRET is not set" in warnings[0] and "API_KEY is not set" in warnings[1]


def test_setting_the_secret_and_the_key_clears_the_warnings(monkeypatch):
    connected(monkeypatch)
    monkeypatch.setenv("ZENDESK_WEBHOOK_SECRET", "s")
    assert len(main.copilot.security_warnings()) == 1
    monkeypatch.setenv("API_KEY", "k")
    assert main.copilot.security_warnings() == []


def test_the_warnings_are_logged_at_startup(monkeypatch, caplog):
    connected(monkeypatch)
    with caplog.at_level("WARNING", logger="support_copilot"):
        main.copilot.log_startup_warnings()
    assert "ZENDESK_WEBHOOK_SECRET is not set" in caplog.text
