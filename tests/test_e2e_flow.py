"""End-to-end flow, run against EVERY ticket system with the same assertions.

Assertions are about the final state the customer/agents would see in the ticket
system, not just about the requests we sent.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from tests.fakes import FakeZammad, FakeZendesk

SAMPLES = json.loads((Path(__file__).parent.parent / "data" / "sample_tickets.json").read_text())


class Env:
    def __init__(self, fake):
        self.fake, self.name = fake, fake.name
        self.c = TestClient(main.app)
        main.systems[fake.name] = fake.make()

    def hook(self, tid, subject, desc, who="Sam"):
        return self.c.post(f"/webhooks/{self.name}", json=self.fake.payload(tid, subject, desc, who))

    def review(self, local_id, **body):
        return self.c.post(f"/tickets/{local_id}/review", json={"reviewer": "monica", **body})

    def outbox(self):
        return list(reversed(self.c.get("/outbox").json()))


@pytest.fixture(params=[FakeZendesk, FakeZammad], ids=["zendesk", "zammad"])
def env(request):
    return Env(request.param())


def test_happy_path_exact_state(env):
    fz = env.fake
    fz.seed(100, tags=["vip", "renewal"])  # tags the agent already set

    r = env.hook(100, "Duplicate charge on invoice", "I was charged twice, I want a refund.")
    assert r.status_code == 201
    local_id = r.json()["id"]

    # --- after triage, BEFORE human review ---
    assert fz.public(100) == []                                  # customer sees nothing
    assert len(fz.internal(100)) == 1 and "pending human review" in fz.internal(100)[0]
    assert fz.priority(100) == fz.PRIO["P3"]
    assert fz.tags(100) == {"vip", "renewal", "ai_triaged", "ai_cat_billing", "ai_team_finance"}
    assert [t["id"] for t in env.c.get("/tickets", params={"status": "pending_review"}).json()] == [local_id]

    # --- human approves with edits ---
    rv = env.review(local_id, action="approved", final_priority="P1",
                    edited_reply="Hi Sam, we refunded the duplicate charge.")
    assert rv.status_code == 200 and rv.json()["sync"] == "sent"

    assert fz.public(100) == ["Hi Sam, we refunded the duplicate charge."]  # exactly the edited text
    assert len(fz.internal(100)) == 1                                       # no extra internal notes
    assert fz.priority(100) == fz.PRIO["P1"]                                # human's priority wins
    assert {"vip", "renewal"} <= fz.tags(100)                               # agent tags survived

    assert env.c.get(f"/tickets/{local_id}").json()["status"] == "approved"
    assert env.c.get("/tickets", params={"status": "pending_review"}).json() == []
    assert [(o["system"], o["kind"], o["mode"]) for o in env.outbox()] == [
        (env.name, "triage_note", "sent"), (env.name, "public_reply", "sent")]


def test_rejected_ticket_never_becomes_public(env):
    fz = env.fake
    fz.seed(200)
    tid = env.hook(200, "Platform is down", "Nobody can access anything, total outage.").json()["id"]
    assert fz.priority(200) == fz.PRIO["P1"]

    rv = env.review(tid, action="rejected")
    assert rv.json()["sync"] == "sent"
    assert fz.public(200) == []                                   # never public
    assert "ai_rejected" in fz.tags(200)
    assert "Handle manually" in fz.internal(200)[-1]
    assert env.c.get(f"/tickets/{tid}").json()["status"] == "rejected"
    assert env.c.get("/metrics").json() == {"reviewed": 0}        # rejections don't count as agreement


def test_webhook_retries_do_not_duplicate_anything(env):
    fz = env.fake
    fz.seed(300)
    ids = {env.hook(300, "Duplicate charge", "refund my invoice please").json()["id"] for _ in range(3)}
    assert len(ids) == 1
    assert len(fz.internal(300)) == 1 and len(env.outbox()) == 1
    assert len(env.c.get("/tickets").json()) == 1


def test_second_review_is_rejected_and_sends_nothing(env):
    fz = env.fake
    fz.seed(400)
    tid = env.hook(400, "How to export a report?", "How do I export the monthly report to CSV?").json()["id"]
    assert env.review(tid, action="approved").status_code == 200
    n = len(fz.requests)
    assert env.review(tid, action="rejected").status_code == 409
    assert len(fz.requests) == n and len(fz.public(400)) == 1
    assert "ai_rejected" not in fz.tags(400)


def test_local_ticket_without_a_ticket_system_makes_no_calls(env):
    tid = env.c.post("/tickets", json={"subject": "Duplicate charge", "body": "refund my invoice"}).json()["id"]
    rv = env.review(tid, action="approved")
    assert rv.status_code == 200 and rv.json()["sync"] is None
    assert env.fake.requests == []


def test_unknown_external_ticket_is_logged_not_lost(env):
    r = env.hook(999, "Duplicate charge", "refund my invoice please")   # 999 not seeded -> system errors
    assert r.status_code == 201
    tid = r.json()["id"]
    rv = env.review(tid, action="approved")
    assert rv.status_code == 200 and rv.json()["sync"] == "error"
    assert env.c.get(f"/tickets/{tid}").json()["status"] == "approved"   # human decision kept
    assert [o["mode"] for o in env.outbox()] == ["error", "error"]


def test_full_batch_invariant_and_metrics(env):
    """All 6 sample tickets: nothing public before review; exact priorities; exact metrics."""
    fz = env.fake
    expected = ["P1", "P3", "P3", "P4", "P4", "P2"]
    local = {}
    for i, t in enumerate(SAMPLES):
        fz.seed(1000 + i)
        local[1000 + i] = env.hook(1000 + i, t["subject"], t["body"], t["customer"]).json()["id"]

    for i, p in enumerate(expected):
        assert fz.priority(1000 + i) == fz.PRIO[p], SAMPLES[i]["subject"]
        assert fz.public(1000 + i) == []                           # INVARIANT before review

    prios = [t["suggestion"]["triage"]["priority"] for t in env.c.get("/tickets").json()]
    assert prios == sorted(prios) and prios[0] == "P1"

    for i, zid in enumerate(local):                                # human edits one reply, one priority
        body = {"action": "approved"}
        if i == 0:
            body["edited_reply"] = "We are on it."
        if i == 1:
            body["final_priority"] = "P2"
        assert env.review(local[zid], **body).status_code == 200

    assert all(len(fz.public(z)) == 1 for z in local)              # exactly one public reply each
    assert fz.public(1000) == ["We are on it."]
    assert len(env.outbox()) == 12                                 # 6 notes + 6 replies, no extras
    assert env.c.get("/metrics").json() == {
        "reviewed": 6, "category_agreement": 1.0,
        "priority_agreement": round(5 / 6, 2), "reply_edit_rate": round(1 / 6, 2)}


def test_same_external_id_in_two_systems_does_not_collide():
    zd, zm = Env(FakeZendesk()), Env(FakeZammad())
    zd.fake.seed(7), zm.fake.seed(7)
    a = zd.hook(7, "Duplicate charge", "refund my invoice").json()
    b = zm.hook(7, "Duplicate charge", "refund my invoice").json()
    assert a["id"] != b["id"] and not a.get("duplicate") and not b.get("duplicate")
    zm.review(b["id"], action="approved", edited_reply="Only for Zammad.")
    assert zm.fake.public(7) == ["Only for Zammad."] and zd.fake.public(7) == []
