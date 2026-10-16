"""Concurrency, security and input-limit behaviour."""
import threading

import pytest
from fastapi.testclient import TestClient

from support_copilot import main
from tests.fakes import FakeZendesk

HOOK = {"ticket_id": "1", "subject": "Duplicate charge on invoice", "description": "refund my invoice please"}


def run_threads(n, fn):
    barrier, results = threading.Barrier(n), [None] * n

    def worker(i):
        barrier.wait()  # release all threads at the same instant
        results[i] = fn(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results


def test_simultaneous_reviews_exactly_one_wins():
    fz = FakeZendesk()
    fz.seed(1)
    main.copilot.systems["zendesk"] = fz.make()
    tid = TestClient(main.app).post("/webhooks/zendesk", json=HOOK).json()["id"]

    codes = run_threads(8, lambda i: TestClient(main.app).post(
        f"/tickets/{tid}/review", json={"action": "approved", "reviewer": f"r{i}", "edited_reply": f"reply {i}"}
    ).status_code)

    assert sorted(codes) == [200] + [409] * 7
    assert len(fz.public(1)) == 1                                            # the customer got ONE reply
    assert main.copilot.conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1


def test_simultaneous_identical_webhooks_create_one_ticket_and_one_note():
    fz = FakeZendesk()
    fz.seed(1)
    main.copilot.systems["zendesk"] = fz.make()

    results = run_threads(8, lambda i: TestClient(main.app).post("/webhooks/zendesk", json=HOOK))

    assert all(r.status_code == 201 for r in results)                        # no 500 from a unique-constraint race
    assert len({r.json()["id"] for r in results}) == 1
    assert sum(1 for r in results if r.json().get("duplicate")) == 7
    assert main.copilot.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
    assert len(fz.internal(1)) == 1


def test_duplicate_detected_after_triage_does_not_push_twice(monkeypatch):
    """Deterministic version of the race: an identical webhook completes while we are still triaging."""
    fz = FakeZendesk()
    fz.seed(1)
    main.copilot.systems["zendesk"] = fz.make()
    real_suggest = main.copilot.suggest

    def suggest_then_lose_the_race(t):
        s = real_suggest(t)
        main.copilot.store(t, s, "zendesk", "1")      # the other delivery wins here
        return s

    monkeypatch.setattr(main.copilot, "suggest", suggest_then_lose_the_race)
    r = TestClient(main.app).post("/webhooks/zendesk", json=HOOK)
    assert r.status_code == 201 and r.json().get("duplicate") is True
    assert fz.requests == []                    # the loser must not write to the ticket system


def test_review_action_must_be_approved_or_rejected():
    c = TestClient(main.app)
    tid = c.post("/tickets", json={"subject": "Duplicate charge", "body": "refund my invoice"}).json()["id"]
    for bad in ("pending_review", "maybe", ""):
        assert c.post(f"/tickets/{tid}/review", json={"action": bad, "reviewer": "m"}).status_code == 422
    assert c.get(f"/tickets/{tid}").json()["status"] == "pending_review"


# ---------------- API key ----------------
@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret-key")
    return TestClient(main.app)


PROTECTED = [("get", "/tickets"), ("get", "/tickets/1"), ("get", "/metrics"), ("get", "/outbox"),
             ("post", "/tickets"), ("post", "/tickets/1/review")]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_api_requires_key_when_configured(secured, method, path):
    assert getattr(secured, method)(path).status_code == 401
    assert getattr(secured, method)(path, headers={"X-API-Key": "wrong"}).status_code == 401
    assert getattr(secured, method)(path, headers={"X-API-Key": "s3cret-key"}).status_code != 401


def test_key_unlocks_the_whole_flow(secured):
    h = {"X-API-Key": "s3cret-key"}
    new = {"subject": "Duplicate charge", "body": "refund please"}
    tid = secured.post("/tickets", json=new, headers=h).json()["id"]
    ok = secured.post(f"/tickets/{tid}/review", json={"action": "approved", "reviewer": "m"}, headers=h)
    assert ok.status_code == 200
    assert secured.get("/metrics", headers=h).json()["reviewed"] == 1


def test_webhooks_and_health_do_not_use_the_api_key(secured):
    assert secured.get("/health").status_code == 200
    assert secured.post("/webhooks/zendesk", json=HOOK).status_code == 201   # authenticated by its own signature


def test_api_is_open_when_no_key_configured():
    c = TestClient(main.app)
    assert c.get("/tickets").status_code == 200 and c.get("/outbox").status_code == 200


def test_health_reports_config_without_leaking_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("API_KEY", "other-secret")
    body = TestClient(main.app).get("/health").json()
    assert body["llm"] is True and body["ticket_systems"] == {"zendesk": False, "zammad": False}
    assert "secret" not in str(body)


# ---------------- limits ----------------
def test_oversized_webhook_is_rejected():
    big = b'{"ticket_id": "1", "subject": "x", "description": "' + b"a" * 1_100_000 + b'"}'
    assert TestClient(main.app).post("/webhooks/zendesk", content=big).status_code == 413


def test_list_limit_and_validation():
    c = TestClient(main.app)
    for i in range(3):
        c.post("/tickets", json={"subject": f"Duplicate charge {i}", "body": "refund my invoice"})
    assert len(c.get("/tickets", params={"limit": 2}).json()) == 2
    assert c.get("/tickets", params={"limit": 0}).status_code == 422
    assert c.get("/outbox", params={"limit": 9999}).status_code == 422


def test_reviewed_tickets_expose_what_was_actually_decided():
    c = TestClient(main.app)
    tid = c.post("/tickets", json={"subject": "Duplicate charge", "body": "refund my invoice"}).json()["id"]
    assert c.get(f"/tickets/{tid}").json()["review"] is None
    c.post(f"/tickets/{tid}/review", json={"action": "approved", "reviewer": "monica", "final_priority": "P1",
                                            "edited_reply": "Edited by a human."})
    t = c.get(f"/tickets/{tid}").json()
    assert t["review"]["final_reply"] == "Edited by a human." and t["review"]["reviewer"] == "monica"
    assert t["review"]["final_priority"] == "P1" and t["suggestion"]["triage"]["priority"] == "P3"
    assert t["suggestion"]["draft_reply"] != "Edited by a human."            # the AI draft is kept as it was
    listed = c.get("/tickets", params={"status": "approved"}).json()
    assert [x["review"]["final_reply"] for x in listed] == ["Edited by a human."]


def test_review_ui_page_is_served_and_never_injects_html():
    r = TestClient(main.app).get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "Review queue" in r.text
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in r.text, f"ticket text is customer-controlled: {sink} would make it an XSS sink"


def test_an_oversized_webhook_is_refused_before_its_body_is_read(monkeypatch):
    from starlette.requests import Request

    async def never(self):
        raise AssertionError("the body must not be read once the declared length is too big")

    monkeypatch.setattr(Request, "body", never)
    r = TestClient(main.app).post("/webhooks/zendesk", content=b"x" * 1_000_001)
    assert r.status_code == 413


# ---------------- what a helpdesk sends is fitted, not refused: a refused webhook is retried and never triaged
def test_a_very_long_ticket_is_triaged_on_its_first_characters_and_says_so():
    fz = FakeZendesk()
    fz.seed(1)
    main.copilot.systems["zendesk"] = fz.make()
    hook = {**HOOK, "description": "I was charged twice, refund please. " + "blah " * 20_000}
    r = TestClient(main.app).post("/webhooks/zendesk", json=hook)
    assert r.status_code == 201
    stored = main.copilot.get_ticket(r.json()["id"])
    assert len(stored["body"]) == 10_000 and stored["suggestion"]["triage"]["category"] == "billing"
    assert "only its first 10,000 characters were used" in fz.internal(1)[0]


@pytest.mark.parametrize("subject,description,stored_subject,stored_body", [
    ("Hi", "ok", "Hi.", "ok."),                                  # shorter than the 3 characters a typed ticket needs
    ("", "My invoice is wrong", "(no subject)", "My invoice is wrong"),
    ("Invoice question", "", "Invoice question", "Invoice question"),   # an empty body is filled in from the subject
    ("  Spaced \n out  ", "Body  text", "Spaced out", "Body  text"),
])
def test_tiny_or_empty_fields_from_a_helpdesk_are_filled_in_not_refused(subject, description, stored_subject,
                                                                       stored_body):
    r = TestClient(main.app).post("/webhooks/zendesk", json={"ticket_id": "5", "subject": subject,
                                                             "description": description})
    assert r.status_code == 201, r.text
    stored = main.copilot.get_ticket(r.json()["id"])
    assert (stored["subject"], stored["body"]) == (stored_subject, stored_body)


def test_a_ticket_typed_into_the_api_is_still_validated():
    c = TestClient(main.app)
    assert c.post("/tickets", json={"subject": "Hi", "body": "ok", "customer": "x"}).status_code == 422
    assert c.post("/tickets", json={"subject": "Hello", "body": "x" * 10_001, "customer": "x"}).status_code == 422


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_an_empty_edited_reply_is_refused_instead_of_sending_a_blank_or_the_ai_draft(blank):
    c = TestClient(main.app)
    tid = c.post("/tickets", json={"subject": "Invoice", "body": "my invoice is wrong", "customer": "A"}).json()["id"]
    r = c.post(f"/tickets/{tid}/review", json={"action": "approved", "reviewer": "ana", "edited_reply": blank})
    assert r.status_code == 422
    assert c.get(f"/tickets/{tid}").json()["status"] == "pending_review"          # nothing was decided
    draft = c.post(f"/tickets/{tid}/review", json={"action": "approved", "reviewer": "ana"})  # no edit: the draft
    assert draft.status_code == 200


def test_the_page_will_not_approve_an_empty_reply_and_does_not_send_one_on_reject():
    from pathlib import Path
    page = (Path(__file__).resolve().parent.parent / "support_copilot" / "static" / "index.html").read_text()
    assert "The reply is empty. Write one, or reject the ticket." in page
    assert "reply.value !== s.draft_reply && reply.value.trim()" in page
