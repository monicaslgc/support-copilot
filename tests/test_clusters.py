"""Duplicate and incident-spike detection: the pure rules, then the whole flow."""
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main
from app.clusters import Item, find_cluster, find_duplicate, jaccard, same_customer
from app.db import connect
from app.models import Review
from tests.fakes import FakeZendesk

T0 = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
OUTAGES = [
    ("Platform is down", "Nobody on my team can access anything, total outage since 9am."),
    ("Cannot reach the platform", "The platform is unavailable for our whole team, is it down?"),
    ("Service unavailable", "The platform is slow and unavailable, is there an outage?"),
    ("Everything is down", "Our users all report the platform is down. Outage?"),
]


def item(id, *, customer="a", category="bug", topic="service-outage", words="x y z", minutes=0, cluster_id=None):
    return Item(id=id, customer=customer, category=category, topic=topic, tokens=frozenset(words.split()),
                created_at=T0 + timedelta(minutes=minutes), cluster_id=cluster_id)


# ---------------- the rules ----------------
def test_same_topic_and_category_in_the_window_join():
    assert find_cluster(item(0, minutes=10), [item(1)]).id == 1


def test_outside_the_window_does_not_join():
    assert find_cluster(item(0, minutes=61), [item(1)]) is None
    assert find_cluster(item(0, minutes=60), [item(1)]).id == 1


def test_same_topic_but_different_category_does_not_join():
    new, other = item(0, category="billing", words="a b c"), item(1, category="bug", words="d e f")
    assert find_cluster(new, [other]) is None


def test_no_topic_means_only_near_identical_text_can_join():
    a = item(1, topic=None, words="login page shows error after password reset")
    assert find_cluster(item(0, topic=None, words="something else entirely"), [a]) is None
    assert find_cluster(item(0, topic=None, words="login page shows error after password reset today"), [a]).id == 1


def test_the_earliest_related_ticket_is_the_parent():
    others = [item(3, minutes=5), item(1, minutes=1), item(2, minutes=3)]
    assert find_cluster(item(0, minutes=6), others).id == 1


def test_duplicate_needs_same_customer_and_category():
    new = item(0, customer="Sam", minutes=5)
    assert find_duplicate(new, [item(1, customer="sam ")]).id == 1                      # case/space-insensitive
    assert find_duplicate(new, [item(1, customer="Ana")]) is None
    assert find_duplicate(new, [item(1, customer="Sam", category="billing")]) is None
    assert find_duplicate(item(0, customer="Sam", minutes=90), [item(1, customer="Sam")]) is None


def test_unknown_customers_never_match():
    assert not same_customer("unknown", "unknown") and not same_customer("", "")
    assert find_duplicate(item(0, customer="unknown"), [item(1, customer="unknown")]) is None


def test_jaccard():
    assert jaccard(frozenset("ab"), frozenset("ab")) == 1 and jaccard(frozenset("ab"), frozenset("cd")) == 0
    assert jaccard(frozenset(), frozenset("a")) == 0


def test_window_and_spike_threshold_can_be_configured(monkeypatch):
    monkeypatch.setenv("CLUSTER_WINDOW_MIN", "5")
    assert find_cluster(item(0, minutes=10), [item(1)]) is None


# ---------------- the flow ----------------
@pytest.fixture
def clock(monkeypatch):
    class Clock:
        minutes = 0

        def __call__(self):
            return (T0 + timedelta(minutes=self.minutes)).isoformat(timespec="seconds")

    c = Clock()
    monkeypatch.setattr(main, "now", c)
    return c


def post(c, customer, n=0, **kw):
    subject, body = OUTAGES[n % len(OUTAGES)]
    r = c.post("/tickets", json={"subject": subject, "body": body, "customer": customer, **kw})
    assert r.status_code == 201
    return r.json()["id"]


def test_a_spike_forms_as_different_customers_report_the_same_outage(clock):
    c = TestClient(main.app)
    first = post(c, "Ana", 0)
    assert c.get(f"/tickets/{first}").json()["cluster"] is None and c.get("/clusters").json() == []

    clock.minutes = 5
    post(c, "Rui", 1)
    t1 = c.get(f"/tickets/{first}").json()["cluster"]                     # the first ticket is linked retroactively
    assert (t1["size"], t1["customers"], t1["kind"], t1["topic"]) == (2, 2, "related", "service-outage")

    clock.minutes = 9
    post(c, "Sam", 2)
    cl = c.get("/clusters").json()
    assert len(cl) == 1 and (cl[0]["size"], cl[0]["customers"], cl[0]["kind"]) == (3, 3, "spike")
    assert cl[0]["topic_title"] == "Service outage or platform unavailable"
    assert cl[0]["pending_ids"] == cl[0]["ticket_ids"] == [first, first + 1, first + 2]
    assert {t["cluster"]["id"] for t in c.get("/tickets").json()} == {first}


def test_one_customer_sending_three_tickets_is_not_an_incident(clock):
    c = TestClient(main.app)
    ids = [post(c, "Ana", n) for n in range(3)]
    cl = c.get("/clusters").json()
    assert (cl[0]["size"], cl[0]["customers"], cl[0]["kind"]) == (3, 1, "related")
    assert c.get(f"/tickets/{ids[1]}").json()["duplicate_of"] == ids[0]
    assert c.post(f"/clusters/{cl[0]['id']}/review", json={"action": "approved", "reviewer": "m"}).status_code == 409


def test_a_ticket_after_the_window_starts_fresh(clock):
    c = TestClient(main.app)
    post(c, "Ana", 0)
    clock.minutes = 61
    late = post(c, "Rui", 1)
    assert c.get(f"/tickets/{late}").json()["cluster"] is None and c.get("/clusters").json() == []


def test_unrelated_tickets_are_not_linked(clock):
    c = TestClient(main.app)
    a = c.post("/tickets", json={"subject": "Duplicate charge", "body": "refund my invoice", "customer": "Ana"}).json()
    b = c.post("/tickets", json={"subject": "How to export a report?", "body": "export monthly report CSV",
                                 "customer": "Rui"}).json()
    assert c.get("/clusters").json() == [] and a["id"] != b["id"]


def test_tickets_system_notes_and_tags_explain_the_link(clock):
    fz = FakeZendesk()
    main.systems["zendesk"] = fz.make()
    c = TestClient(main.app)
    for i, who in enumerate(["Ana", "Rui", "Sam"]):
        clock.minutes = i * 3
        fz.seed(100 + i)
        subject, body = OUTAGES[i]
        assert c.post("/webhooks/zendesk", json=fz.payload(100 + i, subject, body, who)).status_code == 201

    assert "POSSIBLE INCIDENT" not in fz.internal(100)[0] and "ai_spike" not in fz.tags(100)
    assert "Related: 2 tickets from 2 customers" in fz.internal(101)[0] and "ai_spike" not in fz.tags(101)
    assert "POSSIBLE INCIDENT: 3 tickets from 3 customers" in fz.internal(102)[0]
    assert '"Service outage or platform unavailable"' in fz.internal(102)[0]
    assert "ai_spike" in fz.tags(102)
    assert fz.public(100) == fz.public(101) == fz.public(102) == []       # linking never reaches a customer


def test_possible_duplicate_is_flagged_for_the_same_customer_only(clock):
    fz = FakeZendesk()
    main.systems["zendesk"] = fz.make()
    c = TestClient(main.app)
    for i, who in enumerate(["Sam", "Sam", "Ana"]):
        clock.minutes = i
        fz.seed(200 + i)
        assert c.post("/webhooks/zendesk", json=fz.payload(200 + i, *OUTAGES[i], who)).status_code == 201
    assert "Possible duplicate of zendesk ticket 200" in fz.internal(201)[0]
    assert "ai_possible_duplicate" in fz.tags(201)
    assert "ai_possible_duplicate" not in fz.tags(200) and "ai_possible_duplicate" not in fz.tags(202)


def _spike_with_zendesk(clock, customers=("Ana", "Rui", "Sam")):
    fz = FakeZendesk()
    main.systems["zendesk"] = fz.make()
    c = TestClient(main.app)
    for i, who in enumerate(customers):
        clock.minutes = i
        fz.seed(300 + i)
        c.post("/webhooks/zendesk", json=fz.payload(300 + i, *OUTAGES[i], who))
    return c, fz, c.get("/clusters").json()[0]


def test_bulk_approval_without_edit_sends_each_customer_their_own_draft(clock):
    c, fz, cl = _spike_with_zendesk(clock)
    r = c.post(f"/clusters/{cl['id']}/review", json={"action": "approved", "reviewer": "monica"})
    assert r.status_code == 200 and r.json()["reviewed"] == cl["ticket_ids"] and r.json()["skipped"] == []
    for ext, who in zip((300, 301, 302), ("Ana", "Rui", "Sam"), strict=True):
        assert len(fz.public(ext)) == 1 and fz.public(ext)[0].startswith(f"Hi {who},")
    assert c.get("/metrics").json()["reviewed"] == 3                       # each ticket is its own review
    assert c.get("/clusters").json() == []                                 # nothing pending any more


def test_bulk_approval_with_an_edit_sends_the_same_text_to_all(clock):
    c, fz, cl = _spike_with_zendesk(clock)
    c.post(f"/clusters/{cl['id']}/review", json={"action": "approved", "reviewer": "monica",
                                                 "edited_reply": "We are aware of the outage and working on it.",
                                                 "final_priority": "P1"})
    for ext in (300, 301, 302):
        assert fz.public(ext) == ["We are aware of the outage and working on it."]
        assert fz.priority(ext) == "urgent"


def test_bulk_rejection_adds_internal_notes_only(clock):
    c, fz, cl = _spike_with_zendesk(clock)
    c.post(f"/clusters/{cl['id']}/review", json={"action": "rejected", "reviewer": "monica"})
    assert all(fz.public(e) == [] and "ai_rejected" in fz.tags(e) for e in (300, 301, 302))


def test_bulk_skips_tickets_already_reviewed_and_keeps_going(clock):
    c, fz, cl = _spike_with_zendesk(clock)
    first = cl["ticket_ids"][0]
    c.post(f"/tickets/{first}/review", json={"action": "approved", "reviewer": "m", "edited_reply": "Handled."})
    r = c.post(f"/clusters/{cl['id']}/review", json={"action": "approved", "reviewer": "m"}).json()
    assert r["reviewed"] == cl["ticket_ids"][1:] and r["skipped"] == []     # only pending ones are even attempted
    assert fz.public(300) == ["Handled."]                                   # the earlier decision is untouched


def test_bulk_skips_a_ticket_a_colleague_reviews_mid_batch(clock, monkeypatch):
    c, fz, cl = _spike_with_zendesk(clock)
    ids, real, calls = cl["pending_ids"], main._apply_review, []

    def racing(tid, r):
        if not calls:  # while we handle the first ticket, a colleague approves the second one
            calls.append(tid)
            real(ids[1], Review(action="approved", reviewer="colleague", edited_reply="Colleague handled it."))
        return real(tid, r)

    monkeypatch.setattr(main, "_apply_review", racing)
    r = c.post(f"/clusters/{cl['id']}/review", json={"action": "approved", "reviewer": "monica"}).json()
    assert r["reviewed"] == [ids[0], ids[2]] and r["skipped"] == [ids[1]]
    assert fz.public(301) == ["Colleague handled it."]                       # no second reply to that customer


def test_bulk_review_edge_cases(clock):
    c, fz, cl = _spike_with_zendesk(clock)
    body = {"action": "approved", "reviewer": "m"}
    assert c.post("/clusters/99999/review", json=body).status_code == 404
    assert c.post(f"/clusters/{cl['id']}/review", json={**body, "action": "pending_review"}).status_code == 422
    assert c.post(f"/clusters/{cl['id']}/review", json=body).status_code == 200
    assert c.post(f"/clusters/{cl['id']}/review", json=body).status_code == 409   # nothing left to review


def test_two_customers_are_related_but_not_bulk_reviewable(clock):
    c = TestClient(main.app)
    post(c, "Ana", 0)
    post(c, "Rui", 1)
    cl = c.get("/clusters").json()[0]
    assert cl["kind"] == "related"
    assert c.post(f"/clusters/{cl['id']}/review", json={"action": "approved", "reviewer": "m"}).status_code == 409


def test_spikes_are_listed_before_smaller_groups(clock):
    c = TestClient(main.app)
    for who in ("Ana", "Rui"):                                             # a pair of billing tickets
        c.post("/tickets", json={"subject": "Duplicate charge on invoice", "body": "refund my invoice",
                                 "customer": who})
    for n, who in enumerate(("Ana2", "Rui2", "Sam2")):                     # and an outage spike
        post(c, who, n)
    assert [x["kind"] for x in c.get("/clusters").json()] == ["spike", "related"]


def test_cluster_endpoints_need_the_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    c = TestClient(main.app)
    assert c.get("/clusters").status_code == 401
    assert c.post("/clusters/1/review", json={"action": "approved", "reviewer": "m"}).status_code == 401
    assert c.get("/clusters", headers={"X-API-Key": "k"}).status_code == 200


def test_simultaneous_reports_always_end_up_in_one_cluster():
    c0 = TestClient(main.app)
    barrier = threading.Barrier(6)

    def worker(i):
        barrier.wait()
        TestClient(main.app).post("/tickets", json={"subject": OUTAGES[i % 4][0], "body": OUTAGES[i % 4][1],
                                                    "customer": f"customer-{i}"})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    tickets = c0.get("/tickets").json()
    assert len(tickets) == 6 and len({t["cluster"]["id"] for t in tickets}) == 1       # nobody missed anybody
    assert tickets[0]["cluster"]["size"] == 6 and tickets[0]["cluster"]["kind"] == "spike"


def test_a_database_from_before_this_feature_is_upgraded(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
            subject TEXT NOT NULL, body TEXT NOT NULL, customer TEXT NOT NULL, suggestion TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_review', system TEXT, external_id TEXT,
            UNIQUE (system, external_id));
        CREATE TABLE reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL,
            created_at TEXT NOT NULL, reviewer TEXT NOT NULL, action TEXT NOT NULL, final_category TEXT,
            final_priority TEXT, final_reply TEXT, ai_category TEXT, ai_priority TEXT);
        INSERT INTO tickets (created_at, subject, body, customer, suggestion)
            VALUES ('2026-10-15T09:00:00+00:00', 's', 'b', 'c', '{}');
    """)
    old.commit()
    old.close()
    conn = connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tickets)")}
    assert {"topic", "cluster_id", "duplicate_of"} <= cols
    assert "ai_reply" in {r["name"] for r in conn.execute("PRAGMA table_info(reviews)")}
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1          # data kept
    connect(path)                                                                   # and it is safe to run again
