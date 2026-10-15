"""End-to-end flow against a *stateful* fake Zendesk.

The fake applies Zendesk update semantics (additional_tags = union, tags = replace),
so assertions are about the final state the customer/agents would actually see,
not just about the requests we sent.
"""
import json
import os

os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ZENDESK_WEBHOOK_SECRET", None)

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.zendesk import ZendeskClient

SAMPLES = json.loads((Path(__file__).parent.parent / "data" / "sample_tickets.json").read_text())


class FakeZendesk:
    def __init__(self):
        self.tickets, self.requests = {}, []

    def seed(self, tid, tags=()):
        self.tickets[tid] = {"tags": set(tags), "priority": None, "type": None,
                             "status": "new", "comments": []}

    def handler(self, req: httpx.Request):
        self.requests.append(req)
        tid = int(req.url.path.split("/")[-1].removesuffix(".json"))
        if tid not in self.tickets:
            return httpx.Response(404)
        t, body = self.tickets[tid], json.loads(req.content)["ticket"]
        t["comments"].append(body["comment"])
        for f in ("priority", "type", "status"):
            if f in body:
                t[f] = body[f]
        if "additional_tags" in body:
            t["tags"] |= set(body["additional_tags"])
        if "tags" in body:
            t["tags"] = set(body["tags"])
        return httpx.Response(200, json={"ticket": {}})

    def public(self, tid):
        return [c["body"] for c in self.tickets[tid]["comments"] if c["public"]]

    def internal(self, tid):
        return [c["body"] for c in self.tickets[tid]["comments"] if not c["public"]]


@pytest.fixture
def env():
    fz = FakeZendesk()
    main.zendesk = ZendeskClient("acme", "a@acme.com", "tok", transport=httpx.MockTransport(fz.handler))
    yield TestClient(main.app), fz
    main.zendesk = ZendeskClient()


def hook(c, tid, subject, desc, who="Sam"):
    return c.post("/webhooks/zendesk", json={"ticket_id": str(tid), "subject": subject,
                                             "description": desc, "requester": who})


def test_happy_path_exact_state(env):
    c, fz = env
    fz.seed(100, tags=["vip", "renewal"])  # tags the agent already set

    r = hook(c, 100, "Duplicate charge on invoice", "I was charged twice, I want a refund.")
    assert r.status_code == 201
    local_id = r.json()["id"]

    # --- after triage, BEFORE human review ---
    z = fz.tickets[100]
    assert fz.public(100) == []                                  # customer sees nothing
    assert len(fz.internal(100)) == 1 and "pending human review" in fz.internal(100)[0]
    assert (z["priority"], z["type"], z["status"]) == ("normal", "question", "new")
    assert z["tags"] == {"vip", "renewal", "ai_triaged", "ai_cat_billing", "ai_team_finance"}
    assert len(fz.requests) == 1

    # queue holds it as pending
    q = c.get("/tickets", params={"status": "pending_review"}).json()
    assert [t["id"] for t in q] == [local_id]

    # --- human approves with edits ---
    rv = c.post(f"/tickets/{local_id}/review", json={
        "action": "approved", "reviewer": "monica", "final_priority": "P2",
        "edited_reply": "Hi Sam, we refunded the duplicate charge."})
    assert rv.status_code == 200 and rv.json()["zendesk_sync"] == "sent"

    assert fz.public(100) == ["Hi Sam, we refunded the duplicate charge."]  # exactly the edited text
    assert (z["priority"], z["status"]) == ("high", "pending")
    assert {"vip", "renewal"} <= z["tags"]                       # agent tags survived
    assert len(fz.requests) == 2                                 # triage note + reply, nothing else

    # local state
    assert c.get(f"/tickets/{local_id}").json()["status"] == "approved"
    assert c.get("/tickets", params={"status": "pending_review"}).json() == []
    assert [(o["kind"], o["mode"]) for o in reversed(c.get("/outbox").json())] == [
        ("triage_note", "sent"), ("public_reply", "sent")]


def test_rejected_ticket_never_becomes_public(env):
    c, fz = env
    fz.seed(200)
    tid = hook(c, 200, "Platform is down", "Nobody can access anything, total outage.").json()["id"]
    assert fz.tickets[200]["priority"] == "urgent" and fz.tickets[200]["type"] == "incident"

    rv = c.post(f"/tickets/{tid}/review", json={"action": "rejected", "reviewer": "monica"})
    assert rv.json()["zendesk_sync"] == "sent"
    assert fz.public(200) == []                                   # never public
    assert "ai_rejected" in fz.tickets[200]["tags"]
    assert "Handle manually" in fz.internal(200)[-1]
    assert c.get(f"/tickets/{tid}").json()["status"] == "rejected"
    assert c.get("/metrics").json() == {"reviewed": 0}            # rejections don't count as agreement


def test_webhook_retries_do_not_duplicate_anything(env):
    c, fz = env
    fz.seed(300)
    ids = {hook(c, 300, "Duplicate charge", "refund my invoice please").json()["id"] for _ in range(3)}
    assert len(ids) == 1
    assert len(fz.requests) == 1 and len(fz.internal(300)) == 1
    assert c.get("/tickets").json().__len__() == 1


def test_second_review_is_rejected_and_sends_nothing(env):
    c, fz = env
    fz.seed(400)
    tid = hook(c, 400, "How to export a report?", "How do I export the monthly report to CSV?").json()["id"]
    assert c.post(f"/tickets/{tid}/review", json={"action": "approved", "reviewer": "a"}).status_code == 200
    n = len(fz.requests)
    assert c.post(f"/tickets/{tid}/review", json={"action": "rejected", "reviewer": "b"}).status_code == 409
    assert len(fz.requests) == n and len(fz.public(400)) == 1
    assert "ai_rejected" not in fz.tickets[400]["tags"]


def test_local_ticket_without_zendesk_makes_no_calls(env):
    c, fz = env
    tid = c.post("/tickets", json={"subject": "Duplicate charge", "body": "refund my invoice"}).json()["id"]
    rv = c.post(f"/tickets/{tid}/review", json={"action": "approved", "reviewer": "m"})
    assert rv.status_code == 200 and rv.json()["zendesk_sync"] is None
    assert fz.requests == []


def test_human_override_of_category_changes_zendesk_type(env):
    c, fz = env
    fz.seed(500)
    tid = hook(c, 500, "Duplicate charge", "refund my invoice please").json()["id"]
    assert fz.tickets[500]["type"] == "question"
    c.post(f"/tickets/{tid}/review", json={"action": "approved", "reviewer": "m",
                                           "final_category": "bug", "final_priority": "P1"})
    assert (fz.tickets[500]["type"], fz.tickets[500]["priority"]) == ("incident", "urgent")


def test_unknown_zendesk_ticket_is_logged_not_lost(env):
    c, fz = env  # ticket 999 not seeded -> fake returns 404
    r = hook(c, 999, "Duplicate charge", "refund my invoice please")
    assert r.status_code == 201
    tid = r.json()["id"]
    rv = c.post(f"/tickets/{tid}/review", json={"action": "approved", "reviewer": "m"})
    assert rv.status_code == 200 and rv.json()["zendesk_sync"] == "error"
    assert c.get(f"/tickets/{tid}").json()["status"] == "approved"   # human decision kept
    assert [o["mode"] for o in c.get("/outbox").json()] == ["error", "error"]


def test_full_batch_invariant_and_metrics(env):
    """All 6 sample tickets: nothing public before review; exact mapping; exact metrics."""
    c, fz = env
    expected = [("urgent", "incident"), ("normal", "question"), ("normal", "question"),
                ("low", "question"), ("low", "task"), ("high", "incident")]
    local = {}
    for i, t in enumerate(SAMPLES):
        zid = 1000 + i
        fz.seed(zid)
        local[zid] = hook(c, zid, t["subject"], t["body"], t["customer"]).json()["id"]

    for i, (pri, typ) in enumerate(expected):
        z = fz.tickets[1000 + i]
        assert (z["priority"], z["type"]) == (pri, typ), SAMPLES[i]["subject"]
        assert fz.public(1000 + i) == []                           # INVARIANT before review

    # queue is sorted: P1 first, then P2, P3s, P4s
    prios = [t["suggestion"]["triage"]["priority"] for t in c.get("/tickets").json()]
    assert prios == sorted(prios) and prios[0] == "P1"

    # human approves all, edits only one reply and one priority
    for i, zid in enumerate(local):
        payload = {"action": "approved", "reviewer": "monica"}
        if i == 0:
            payload["edited_reply"] = "We are on it."
        if i == 1:
            payload["final_priority"] = "P2"
        assert c.post(f"/tickets/{local[zid]}/review", json=payload).status_code == 200

    assert all(len(fz.public(z)) == 1 for z in local)             # exactly one public reply each
    assert fz.public(1000) == ["We are on it."]
    assert len(fz.requests) == 12                                 # 6 notes + 6 replies, no extras
    assert c.get("/metrics").json() == {
        "reviewed": 6, "category_agreement": 1.0,
        "priority_agreement": round(5 / 6, 2), "reply_edit_rate": round(1 / 6, 2)}
