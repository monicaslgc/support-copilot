import base64
import hashlib
import hmac
import json
import os

os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["TRIAGE_DB"] = ":memory:"

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.zendesk import ZendeskClient, verify_signature

WEBHOOK = {"ticket_id": "4711", "subject": "Duplicate charge on invoice",
           "description": "I was charged twice, I want a refund.", "requester": "Sam"}


@pytest.fixture
def zd():
    """Fake Zendesk API: records every request it receives."""
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, json={"ticket": {}})

    main.zendesk = ZendeskClient("acme", "agent@acme.com", "tok", transport=httpx.MockTransport(handler))
    yield calls
    main.zendesk = ZendeskClient()  # back to dry-run


def body(req):
    return json.loads(req.content)["ticket"]


def test_webhook_posts_internal_note_priority_and_additive_tags(zd):
    c = TestClient(main.app)
    r = c.post("/webhooks/zendesk", json=WEBHOOK)
    assert r.status_code == 201 and r.json()["status"] == "pending_review"
    assert len(zd) == 1
    req, t = zd[0], body(zd[0])
    assert req.method == "PUT" and str(req.url) == "https://acme.zendesk.com/api/v2/tickets/4711.json"
    expected = base64.b64encode(b"agent@acme.com/token:tok").decode()
    assert req.headers["authorization"] == f"Basic {expected}"
    assert t["comment"]["public"] is False          # customer must not see it
    assert t["priority"] == "normal" and t["type"] == "question"
    assert "ai_cat_billing" in t["additional_tags"] and "tags" not in t  # never replace agent tags


def test_webhook_is_idempotent_on_retry(zd):
    c = TestClient(main.app)
    first = c.post("/webhooks/zendesk", json={**WEBHOOK, "ticket_id": "5000"}).json()
    again = c.post("/webhooks/zendesk", json={**WEBHOOK, "ticket_id": "5000"}).json()
    assert again == {"id": first["id"], "duplicate": True}
    assert len(zd) == 1


def test_approval_sends_public_reply_with_human_edits(zd):
    c = TestClient(main.app)
    tid = c.post("/webhooks/zendesk", json={**WEBHOOK, "ticket_id": "6000"}).json()["id"]
    r = c.post(f"/tickets/{tid}/review", json={
        "action": "approved", "reviewer": "monica", "final_priority": "P2",
        "edited_reply": "Hi Sam, refund approved."})
    assert r.json()["zendesk_sync"] == "sent"
    t = body(zd[-1])
    assert t["comment"] == {"body": "Hi Sam, refund approved.", "public": True}
    assert t["priority"] == "high" and t["status"] == "pending"


def test_rejection_adds_internal_note_only(zd):
    c = TestClient(main.app)
    tid = c.post("/webhooks/zendesk", json={**WEBHOOK, "ticket_id": "7000"}).json()["id"]
    c.post(f"/tickets/{tid}/review", json={"action": "rejected", "reviewer": "monica"})
    t = body(zd[-1])
    assert t["comment"]["public"] is False and t["additional_tags"] == ["ai_rejected"]


def test_zendesk_outage_does_not_lose_ticket_or_decision():
    main.zendesk = ZendeskClient("acme", "a@b.c", "tok",
                                 transport=httpx.MockTransport(lambda req: httpx.Response(503)))
    try:
        c = TestClient(main.app)
        r = c.post("/webhooks/zendesk", json={**WEBHOOK, "ticket_id": "8000"})
        assert r.status_code == 201
        rv = c.post(f"/tickets/{r.json()['id']}/review", json={"action": "approved", "reviewer": "m"})
        assert rv.status_code == 200 and rv.json()["zendesk_sync"] == "error"
        assert {o["mode"] for o in c.get("/outbox").json() if o["zendesk_ticket_id"] == 8000} == {"error"}
    finally:
        main.zendesk = ZendeskClient()


def test_dry_run_without_credentials():
    c = TestClient(main.app)
    c.post("/webhooks/zendesk", json={**WEBHOOK, "ticket_id": "9000"})
    row = next(o for o in c.get("/outbox").json() if o["zendesk_ticket_id"] == 9000)
    assert row["mode"] == "dry_run" and row["payload"]["ticket"]["comment"]["public"] is False


def test_signature_verification(monkeypatch):
    monkeypatch.setenv("ZENDESK_WEBHOOK_SECRET", "s3cret")
    c = TestClient(main.app)
    raw = json.dumps({**WEBHOOK, "ticket_id": "9100"}).encode()
    ts = "2026-10-15T10:00:00Z"
    sig = base64.b64encode(hmac.new(b"s3cret", ts.encode() + raw, hashlib.sha256).digest()).decode()
    h = {"content-type": "application/json", "X-Zendesk-Webhook-Signature-Timestamp": ts}
    assert c.post("/webhooks/zendesk", content=raw, headers=h).status_code == 401
    assert c.post("/webhooks/zendesk", content=raw, headers={**h, "X-Zendesk-Webhook-Signature": "bad"}).status_code == 401
    assert c.post("/webhooks/zendesk", content=raw, headers={**h, "X-Zendesk-Webhook-Signature": sig}).status_code == 201
    assert verify_signature("s3cret", ts, raw, sig)


def test_bad_payload():
    c = TestClient(main.app)
    assert c.post("/webhooks/zendesk", json={"ticket_id": "abc", "subject": "x"}).status_code == 422
