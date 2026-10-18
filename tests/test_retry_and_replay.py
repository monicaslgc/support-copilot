"""Failed sends can be retried from the outbox, and a signed Zendesk webhook that is too old is refused."""
import base64
import hashlib
import hmac
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from support_copilot import main
from support_copilot.db import connect
from support_copilot.ticketsystems.zendesk import max_age_seconds, timestamp_is_fresh
from tests.fakes import FakeZammad, FakeZendesk

ZD = {"ticket_id": "9100", "subject": "Duplicate charge", "description": "I was charged twice, refund please",
      "requester": "Ana"}
SUBJECT, BODY = "Duplicate charge", "refund my invoice"


# --- webhook timestamp age ------------------------------------------------------------------------------------

NOW = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("timestamp,fresh", [
    ("2026-10-16T10:00:00Z", True),
    ("2026-10-16T09:55:01Z", True),            # 4 min 59 s old
    ("2026-10-16T09:55:00Z", True),            # exactly the limit
    ("2026-10-16T09:54:59Z", False),           # one second too old
    ("2026-10-16T10:05:01Z", False),           # too far in the future
    ("2026-10-16T10:00:00+00:00", True),
    ("2026-10-16T11:00:00+01:00", True),       # an offset is converted, not ignored
    ("2026-10-16T10:00:00", True),             # no zone: taken as UTC
    ("", False), ("ts", False), ("1760608800", False),
])
def test_timestamp_is_fresh(timestamp, fresh):
    assert timestamp_is_fresh(timestamp, 300, NOW) is fresh


def test_age_zero_turns_the_check_off():
    assert timestamp_is_fresh("anything", 0, NOW)


@pytest.mark.parametrize("value,expected", [(None, 300), ("60", 60), ("0", 0), ("-5", 0), ("soon", 300)])
def test_max_age_setting(monkeypatch, value, expected):
    monkeypatch.delenv("WEBHOOK_MAX_AGE_SECONDS", raising=False)
    if value is not None:
        monkeypatch.setenv("WEBHOOK_MAX_AGE_SECONDS", value)
    assert max_age_seconds() == expected


_ids = iter(range(1000, 10**6))


def _post(c, ts):
    raw = json.dumps({**ZD, "ticket_id": str(next(_ids))}).encode()
    sig = base64.b64encode(hmac.new(b"s3cret", ts.encode() + raw, hashlib.sha256).digest()).decode()
    return c.post("/webhooks/zendesk", content=raw, headers={
        "content-type": "application/json", "X-Zendesk-Webhook-Signature-Timestamp": ts,
        "X-Zendesk-Webhook-Signature": sig})


def _iso(delta):
    return (datetime.now(UTC) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_correctly_signed_but_old_webhook_is_refused(monkeypatch):
    monkeypatch.setenv("ZENDESK_WEBHOOK_SECRET", "s3cret")
    c = TestClient(main.app)
    assert _post(c, _iso(timedelta(0))).status_code == 201
    assert _post(c, _iso(-timedelta(minutes=10))).status_code == 401
    assert _post(c, _iso(timedelta(minutes=10))).status_code == 401
    assert len(c.get("/tickets").json()) == 1                       # the replay created nothing


def test_the_age_limit_is_configurable(monkeypatch):
    monkeypatch.setenv("ZENDESK_WEBHOOK_SECRET", "s3cret")
    c = TestClient(main.app)
    monkeypatch.setenv("WEBHOOK_MAX_AGE_SECONDS", "0")
    assert _post(c, _iso(-timedelta(hours=2))).status_code == 201
    monkeypatch.setenv("WEBHOOK_MAX_AGE_SECONDS", "3600")
    assert _post(c, _iso(-timedelta(hours=2))).status_code == 401
    assert _post(c, _iso(-timedelta(minutes=30))).status_code == 201


def test_a_valid_signature_with_an_unreadable_timestamp_is_refused(monkeypatch):
    monkeypatch.setenv("ZENDESK_WEBHOOK_SECRET", "s3cret")
    assert _post(TestClient(main.app), "yesterday").status_code == 401


def test_without_a_secret_the_timestamp_is_not_looked_at():
    assert TestClient(main.app).post("/webhooks/zendesk", json=ZD).status_code == 201


# --- outbox retry ---------------------------------------------------------------------------------------------

@pytest.fixture(params=[FakeZendesk, FakeZammad], ids=["zendesk", "zammad"])
def env(request):
    fake = request.param()
    main.copilot.systems[fake.name] = fake.make()
    return fake, TestClient(main.app)


def _failed_entry(fake, c):
    """A ticket the helpdesk does not know yet: the note we try to write fails, and the failure is logged."""
    assert c.post(f"/webhooks/{fake.name}", json=fake.payload(777, SUBJECT, BODY, "Sam")).status_code == 201
    entry = c.get("/outbox").json()[0]
    assert entry["mode"] == "error"
    return entry


def test_a_failed_send_records_what_it_tried_to_send(env):
    fake, c = env
    entry = _failed_entry(fake, c)
    assert entry["error"] and entry["payload"]["requests"]
    assert entry["payload"]["kind"] == "triage_note" and entry["retry_of"] is None


def test_retry_sends_it_again_once_the_cause_is_fixed(env):
    fake, c = env
    entry = _failed_entry(fake, c)
    assert 777 not in fake.tickets
    fake.seed(777)                                                    # the ticket now exists in the helpdesk
    r = c.post(f"/outbox/{entry['id']}/retry")
    assert r.status_code == 200 and r.json() == {"retry_of": entry["id"], "mode": "sent", "error": None}
    assert any("AI triage" in n for n in fake.internal(777))          # the internal note arrived
    assert fake.public(777) == []                                     # and nothing reached the customer
    latest = c.get("/outbox").json()[0]
    assert latest["mode"] == "sent" and latest["retry_of"] == entry["id"] and latest["kind"] == "triage_note"


def test_retry_that_fails_again_is_logged_and_can_be_tried_again(env):
    fake, c = env
    entry = _failed_entry(fake, c)
    r = c.post(f"/outbox/{entry['id']}/retry")                        # the ticket is still unknown
    assert r.status_code == 200 and r.json()["mode"] == "error" and r.json()["error"]
    second = c.get("/outbox").json()[0]
    assert second["mode"] == "error" and second["retry_of"] == entry["id"] and second["payload"]["requests"]
    fake.seed(777)
    assert c.post(f"/outbox/{second['id']}/retry").json()["mode"] == "sent"   # a retry of the retry


def test_a_sent_retry_cannot_be_repeated(env):
    fake, c = env
    entry = _failed_entry(fake, c)
    fake.seed(777)
    assert c.post(f"/outbox/{entry['id']}/retry").status_code == 200
    again = c.post(f"/outbox/{entry['id']}/retry")
    assert again.status_code == 409 and "already sent" in again.json()["detail"]
    assert len(fake.internal(777)) == 1                               # no second note


def test_only_failed_entries_can_be_retried(env):
    fake, c = env
    fake.seed(5)
    c.post(f"/webhooks/{fake.name}", json=fake.payload(5, SUBJECT, BODY, "Sam"))
    sent = c.get("/outbox").json()[0]
    assert sent["mode"] == "sent"
    r = c.post(f"/outbox/{sent['id']}/retry")
    assert r.status_code == 409 and "nothing to retry" in r.json()["detail"]


def test_unknown_entry_and_entry_without_a_recorded_request(env):
    fake, c = env
    assert c.post("/outbox/12345/retry").status_code == 404
    entry = _failed_entry(fake, c)
    with main.copilot._transaction() as conn:                         # an entry written before requests were kept
        conn.execute("UPDATE outbox SET payload = ? WHERE id = ?", (json.dumps({"kind": "triage_note"}), entry["id"]))
    r = c.post(f"/outbox/{entry['id']}/retry")
    assert r.status_code == 409 and "no recorded request" in r.json()["detail"]


def test_a_ticket_system_that_is_gone_is_404(env):
    fake, c = env
    entry = _failed_entry(fake, c)
    del main.copilot.systems[fake.name]
    assert c.post(f"/outbox/{entry['id']}/retry").status_code == 404


def test_retry_without_credentials_is_a_dry_run_and_does_not_use_up_the_entry():
    fake = FakeZendesk()
    system = main.copilot.systems["zendesk"] = fake.make()
    c = TestClient(main.app)
    entry = _failed_entry(fake, c)
    system.api_token = None                                           # credentials gone: nothing can be sent
    assert c.post(f"/outbox/{entry['id']}/retry").json()["mode"] == "dry_run"
    system.api_token = "tok"
    fake.seed(777)
    assert c.post(f"/outbox/{entry['id']}/retry").json()["mode"] == "sent"


def test_retry_needs_the_api_key_when_one_is_set(monkeypatch):
    monkeypatch.setattr(main.copilot, "api_key", "k")
    c = TestClient(main.app)
    assert c.post("/outbox/1/retry").status_code == 401
    assert c.post("/outbox/1/retry", headers={"X-API-Key": "k"}).status_code == 404


def test_an_old_database_gets_the_new_column(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, "
                "system TEXT NOT NULL, external_id TEXT NOT NULL, kind TEXT NOT NULL, mode TEXT NOT NULL, "
                "payload TEXT NOT NULL, error TEXT)")
    old.commit()
    old.close()
    conn = connect(str(path))
    assert "retry_of" in {r["name"] for r in conn.execute("PRAGMA table_info(outbox)")}
