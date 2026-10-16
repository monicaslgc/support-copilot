"""Adapter-specific behaviour: wire format, auth, signatures, dry-run, payload parsing."""
import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from support_copilot import main
from support_copilot.models import Category, Priority
from support_copilot.ticketsystems import WebhookError, ZammadSystem
from support_copilot.ticketsystems.zendesk import verify_signature as zd_verify
from tests.fakes import FakeZammad, FakeZendesk

ZD = {"ticket_id": "4711", "subject": "Duplicate charge on invoice",
      "description": "I was charged twice, I want a refund.", "requester": "Sam"}
ZM = {"ticket": {"id": 55, "title": "Duplicate charge on invoice", "customer": "sam@x.com"},
      "article": {"body": "I was charged twice, I want a refund.", "from": "Sam <sam@x.com>"}}


def bodies(fake):
    return [json.loads(r.content) for r in fake.requests]


# ---------------- Zendesk ----------------
def test_zendesk_wire_format():
    fz = FakeZendesk()
    fz.seed(4711)
    main.copilot.systems["zendesk"] = fz.make()
    TestClient(main.app).post("/webhooks/zendesk", json=ZD)
    req, t = fz.requests[0], bodies(fz)[0]["ticket"]
    assert req.method == "PUT" and str(req.url) == "https://acme.zendesk.com/api/v2/tickets/4711.json"
    assert t["comment"]["public"] is False
    assert (t["priority"], t["type"]) == ("normal", "question")
    assert "ai_cat_billing" in t["additional_tags"] and "tags" not in t   # never replace agent tags


def test_zendesk_public_reply_sets_status_pending_and_type():
    fz = FakeZendesk()
    fz.seed(4711)
    main.copilot.systems["zendesk"] = fz.make()
    c = TestClient(main.app)
    tid = c.post("/webhooks/zendesk", json=ZD).json()["id"]
    c.post(f"/tickets/{tid}/review", json={"action": "approved", "reviewer": "m",
                                           "final_category": "bug", "final_priority": "P1"})
    z = fz.tickets[4711]
    assert (z["status"], z["type"], z["priority"]) == ("pending", "incident", "urgent")


def test_zendesk_signature(monkeypatch):
    monkeypatch.setenv("ZENDESK_WEBHOOK_SECRET", "s3cret")
    c = TestClient(main.app)
    raw = json.dumps({**ZD, "ticket_id": "9100"}).encode()
    ts = "2026-10-15T10:00:00Z"
    sig = base64.b64encode(hmac.new(b"s3cret", ts.encode() + raw, hashlib.sha256).digest()).decode()
    h = {"content-type": "application/json", "X-Zendesk-Webhook-Signature-Timestamp": ts}
    assert c.post("/webhooks/zendesk", content=raw, headers=h).status_code == 401
    bad = {**h, "X-Zendesk-Webhook-Signature": "bad"}
    good = {**h, "X-Zendesk-Webhook-Signature": sig}
    assert c.post("/webhooks/zendesk", content=raw, headers=bad).status_code == 401
    assert c.post("/webhooks/zendesk", content=raw, headers=good).status_code == 201
    assert zd_verify("s3cret", ts, raw, sig)


def test_zendesk_bad_payload():
    assert TestClient(main.app).post("/webhooks/zendesk", json={"ticket_id": "abc", "subject": "x"}).status_code == 422


# ---------------- Zammad ----------------
def test_zammad_wire_format_internal_note():
    fz = FakeZammad()
    fz.seed(55, tags=["vip"])
    main.copilot.systems["zammad"] = fz.make()
    r = TestClient(main.app).post("/webhooks/zammad", json=ZM)
    assert r.status_code == 201
    sent = [(q.method, q.url.path) for q in fz.requests]
    assert sent[:2] == [("PUT", "/api/v1/tickets/55"), ("POST", "/api/v1/ticket_articles")]
    assert all(p == "/api/v1/tags/add" for _, p in sent[2:]) and len(sent) == 6   # 4 AI tags
    b = bodies(fz)
    assert b[0] == {"priority_id": 2}                                    # P3 -> normal
    assert b[1]["internal"] is True and b[1]["type"] == "note" and b[1]["ticket_id"] == 55
    assert all(x["object"] == "Ticket" and x["o_id"] == 55 for x in b[2:])
    assert fz.tags(55) >= {"vip", "ai_triaged", "ai_cat_billing"}         # additive
    assert fz.requests[0].headers["authorization"] == "Token token=tok"


def test_zammad_public_reply_is_not_internal():
    fz = FakeZammad()
    fz.seed(55)
    main.copilot.systems["zammad"] = fz.make()
    c = TestClient(main.app)
    tid = c.post("/webhooks/zammad", json=ZM).json()["id"]
    fz.requests.clear()
    c.post(f"/tickets/{tid}/review", json={"action": "approved", "reviewer": "m", "edited_reply": "Done."})
    b = bodies(fz)
    assert [q.url.path for q in fz.requests] == ["/api/v1/tickets/55", "/api/v1/ticket_articles"]
    assert b[1]["internal"] is False and b[1]["body"] == "Done."


def test_zammad_p1_and_p2_both_map_to_high():
    s = ZammadSystem()  # dry-run
    for p in (Priority.P1, Priority.P2):
        res = s.post_public_reply("1", "x", priority=p, category=Category.BUG)
        assert res["requests"][0]["json"] == {"priority_id": 3}


def test_zammad_signature(monkeypatch):
    monkeypatch.setenv("ZAMMAD_WEBHOOK_SECRET", "s3cret")
    c = TestClient(main.app)
    raw = json.dumps(ZM).encode()
    sig = "sha1=" + hmac.new(b"s3cret", raw, hashlib.sha1).hexdigest()
    h = {"content-type": "application/json"}
    assert c.post("/webhooks/zammad", content=raw, headers=h).status_code == 401
    assert c.post("/webhooks/zammad", content=raw, headers={**h, "X-Hub-Signature": "sha1=bad"}).status_code == 401
    assert c.post("/webhooks/zammad", content=raw, headers={**h, "X-Hub-Signature": sig}).status_code == 201


def test_zammad_payload_parsing():
    s = ZammadSystem()
    t = s.parse_webhook(json.dumps(ZM).encode())
    assert (t.external_id, t.subject, t.requester) == ("55", "Duplicate charge on invoice", "Sam <sam@x.com>")
    # no article -> falls back to the title as body; no author -> customer
    t = s.parse_webhook(json.dumps({"ticket": {"id": 1, "title": "Help", "customer": "a@b.c"}}).encode())
    assert (t.body, t.requester) == ("Help", "a@b.c")
    for bad in (b"not json", b"{}", b'{"ticket": {"id": 1}}', b"[]"):
        with pytest.raises(WebhookError):
            s.parse_webhook(bad)
    assert TestClient(main.app).post("/webhooks/zammad", json={"ticket": {}}).status_code == 422


def test_zammad_url_trailing_slash_is_normalised():
    assert ZammadSystem("https://help.example.com/", "t").base_url == "https://help.example.com"


# ---------------- common ----------------
@pytest.mark.parametrize("name,payload", [("zendesk", ZD), ("zammad", ZM)])
def test_dry_run_without_credentials(name, payload):
    c = TestClient(main.app)
    c.post(f"/webhooks/{name}", json=payload)
    row = c.get("/outbox").json()[0]
    assert row["mode"] == "dry_run" and row["system"] == name
    assert row["payload"]["requests"], "dry-run must record the exact requests that would be sent"


def test_unknown_system_is_404():
    assert TestClient(main.app).post("/webhooks/freshdesk", json={}).status_code == 404


def test_systems_are_built_from_env(monkeypatch):
    monkeypatch.setenv("ZAMMAD_URL", "https://z.example.com")
    monkeypatch.setenv("ZAMMAD_TOKEN", "t")
    from support_copilot.ticketsystems import build_systems
    s = build_systems()
    assert s["zammad"].configured and not s["zendesk"].configured
