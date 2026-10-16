"""One test per user story in docs/user-stories.md, each checking that story's acceptance criteria.

The names are referenced from the document and tests/test_docs.py checks that every story has its test
and every test has its story, so the stories cannot drift away from what the code does.
"""
import base64
import hashlib
import hmac
import json
import sys

import httpx
from fastapi.testclient import TestClient

from app import main
from app.ticketsystems import ZendeskSystem
from tests.fakes import FakeZammad, FakeZendesk
from tests.test_clusters import OUTAGES
from tests.test_llm import GOOD, FakeAnthropic, block, reply

BILLING = ("Duplicate charge on invoice", "I was charged twice this month, I want a refund.")


def model_replies(monkeypatch, draft: str, triage: dict | None = None) -> FakeAnthropic:
    """A stand-in for the model: a triage for the triage call and `draft` for the drafting call."""
    fake = FakeAnthropic()
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    def create(**kw):
        fake.calls.append(kw)
        is_triage = "triage customer support tickets" in kw["system"]
        return reply(block("text", json.dumps({**GOOD, **(triage or {})}) if is_triage else draft))

    fake.messages.create = create
    return fake


def ticket(c, subject, body, customer="unknown") -> dict:
    r = c.post("/tickets", json={"subject": subject, "body": body, "customer": customer})
    assert r.status_code == 201
    return r.json()


def approve(c, tid, **body):
    r = c.post(f"/tickets/{tid}/review", json={"action": "approved", "reviewer": "ana", **body})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------------------- the agent
def test_us01_a_new_ticket_arrives_triaged_with_a_draft():
    fz = FakeZammad()
    for i in (1, 2):
        fz.seed(i)
    main.systems["zammad"] = fz.make()
    c = TestClient(main.app)

    arrived = c.post("/webhooks/zammad", json=fz.payload(1, *BILLING, "Sam")).json()["suggestion"]
    outage = "Nobody can access anything, total outage."
    c.post("/webhooks/zammad", json=fz.payload(2, "Platform is down", outage, "Ana"))

    triage = arrived["triage"]                                                   # category, priority, team, draft
    assert (triage["category"], triage["priority"], triage["team"]) == ("billing", "P3", "finance")
    assert arrived["draft_reply"].strip()
    assert "invoices" in [a["id"] for a in arrived["articles"]]                  # the matching help article
    queue = c.get("/tickets", params={"status": "pending_review"}).json()        # the outage came second, is first
    assert [t["suggestion"]["triage"]["priority"] for t in queue] == ["P1", "P3"]


def test_us02_nothing_reaches_the_customer_until_approved():
    fz = FakeZendesk()
    for i in (1, 2):
        fz.seed(i)
    main.systems["zendesk"] = fz.make()
    c = TestClient(main.app)
    first = c.post("/webhooks/zendesk", json=fz.payload(1, *BILLING, "Sam")).json()["id"]
    second = c.post("/webhooks/zendesk", json=fz.payload(2, *BILLING, "Ana")).json()["id"]

    assert fz.public(1) == fz.public(2) == []                                    # before a decision: nothing public
    assert len(fz.internal(1)) == 1                                              # only the internal note

    approve(c, first, edited_reply="Hi Sam, we have found the duplicate charge.")
    assert fz.public(1) == ["Hi Sam, we have found the duplicate charge."]       # exactly the approved text

    c.post(f"/tickets/{second}/review", json={"action": "rejected", "reviewer": "ana"})
    assert fz.public(2) == [] and "ai_rejected" in fz.tags(2)                    # nothing sent, tagged for a person


def test_us03_during_an_outage_everyone_is_answered_at_once():
    c = TestClient(main.app)
    ids = [ticket(c, s, b, who)["id"] for (s, b), who in zip(OUTAGES, ("Ana", "Rui", "Sam", "Joana"), strict=True)]
    incident = c.get("/clusters").json()[0]
    assert incident["kind"] == "spike" and incident["customers"] == 4            # one possible incident

    done = c.post(f"/clusters/{incident['id']}/review", json={"action": "approved", "reviewer": "ana"}).json()
    assert done["reviewed"] == ids                                               # every pending ticket answered
    assert all(c.get(f"/tickets/{t}").json()["review"]["reviewer"] == "ana" for t in ids)   # each its own review

    words = " ".join(f"zq{i}" for i in range(6))                                 # one customer, the same thing twice
    a = ticket(c, f"Widget {words}", f"My widget {words} refuses to sync", "Leo")["id"]
    b = ticket(c, f"Widget {words}", f"My widget {words} refuses to sync", "Leo")["id"]
    second = c.get(f"/tickets/{b}").json()
    assert second["duplicate_of"] == a and second["cluster"]["kind"] == "related"
    refused = c.post(f"/clusters/{second['cluster']['id']}/review", json={"action": "approved", "reviewer": "ana"})
    assert refused.status_code == 409                                            # not an incident: no bulk answer


def test_us04_upset_and_repeat_customers_are_visible():
    c = TestClient(main.app)
    earlier = ticket(c, "Exportação falha", "Não consigo exportar o relatório mensal para CSV.", "Rui")["id"]
    upset = ticket(c, "Isto é inaceitável", "A exportação continua a falhar, é a terceira vez! Péssimo serviço, "
                   "quero falar com um responsável.", "Rui")
    triage = upset["suggestion"]["triage"]
    assert triage["sentiment"] == "negative"                                     # a routine ticket moves up, with why
    assert triage["priority"] == "P2" and "Raised from P3" in triage["rationale"]

    outage = ticket(c, "A plataforma está em baixo", "Total outage, inaceitável!! Ninguém consegue aceder.")
    assert outage["suggestion"]["triage"]["priority"] == "P1"                    # a P1 never moves

    history = c.get(f"/tickets/{upset['id']}").json()["history"]
    assert [h["id"] for h in history["recent"]] == [earlier]                     # his earlier ticket is listed
    assert c.get(f"/tickets/{outage['id']}").json()["history"]["count"] == 0     # unknown: no history


def test_us05_promises_are_flagged_before_they_are_sent(monkeypatch):
    model_replies(monkeypatch, "Hi, we will refund you in full today.")           # the model promises a refund
    fz = FakeZendesk()
    fz.seed(1)
    main.systems["zendesk"] = fz.make()
    c = TestClient(main.app)

    made = c.post("/webhooks/zendesk", json=fz.payload(1, *BILLING, "Sam")).json()
    suggestion = made["suggestion"]
    assert "refund" not in suggestion["draft_reply"].lower()                     # replaced by the standard reply
    assert "standard reply was used" in suggestion["notices"][0]                 # and the agent is told why

    flagged = c.post("/check-reply", json={"text": "We will refund you in full."}).json()["warnings"]
    assert [(w["kind"], w["severity"]) for w in flagged] == [("refund", "hard")]  # flagged as the agent types
    sent = approve(c, made["id"], edited_reply="Hi Sam, we have refunded the charge.")
    assert [w["kind"] for w in sent["warnings"]] == ["refund"]                   # still goes through, with the warning
    assert fz.public(1) == ["Hi Sam, we have refunded the charge."]

    soft = c.post("/check-reply", json={"text": "It costs €49 and we reply within 2 days."}).json()["warnings"]
    assert {w["severity"] for w in soft} == {"soft"}                             # an amount or a deadline: gently


# ------------------------------------------------------------------------------------- the customer
def test_us06_answered_in_my_language_with_my_details_masked(monkeypatch):
    fake = model_replies(monkeypatch, "Olá Ana, estamos a tratar da cobrança duplicada.")
    c = TestClient(main.app)
    body = "Fui cobrada duas vezes na fatura. Escreva para ana.silva@example.com. Cartão 4111 1111 1111 1111."
    made = ticket(c, "Cobrança duplicada na fatura", body, "Ana")

    assert made["suggestion"]["triage"]["language"] == "pt"
    assert made["suggestion"]["draft_reply"].startswith("Olá Ana")               # the draft is in Portuguese
    sent_to_model = " ".join(call["messages"][0]["content"] for call in fake.calls)
    assert "ana.silva@example.com" not in sent_to_model and "4111 1111 1111 1111" not in sent_to_model
    assert "[EMAIL]" in sent_to_model and "[CARD]" in sent_to_model              # masked before the model
    assert "ana.silva@example.com" in c.get(f"/tickets/{made['id']}").json()["body"]   # the original is kept
    assert "Masked before sending to the AI" in made["suggestion"]["notices"][0]  # and the agent is told


def test_us06b_an_upset_customer_gets_an_apology_in_their_language():
    c = TestClient(main.app)
    draft = ticket(c, "Isto é inaceitável", "Péssimo serviço, quero falar com um responsável!!",
                   "Rui")["suggestion"]["draft_reply"]
    assert draft.startswith("Olá") and "Lamentamos" in draft


# ------------------------------------------------------------------------------------ the support lead
def test_us07_trust_is_earned_per_category_and_never_automated():
    c = TestClient(main.app)

    def clean_reviews(n):
        for _ in range(n):
            approve(c, ticket(c, *BILLING)["id"])

    clean_reviews(3)
    row = c.get("/metrics/breakdown").json()["by_category"][0]
    assert row["clean_rate"] == 1.0 and row["clean_rate_lower_bound"] < 0.5      # three of three proves little
    assert row["automation_candidate"] is False

    clean_reviews(32)                                                            # a long clean record
    assert c.get("/metrics/breakdown").json()["by_category"][0]["automation_candidate"] is True

    new = ticket(c, *BILLING)                                                    # but the app still asks a person
    after = c.get(f"/tickets/{new['id']}").json()
    assert after["status"] == "pending_review" and after["review"] is None


def test_us08_the_reasons_for_corrections_are_recorded_and_counted():
    c = TestClient(main.app)
    approve(c, ticket(c, *BILLING)["id"], final_priority="P2", reasons=["wrong_priority"], note="urgent customer")
    approve(c, ticket(c, *BILLING)["id"], edited_reply="Hi, sorted.", reasons=["tone"])
    c.post(f"/tickets/{ticket(c, *BILLING)['id']}/review", json={"action": "rejected", "reviewer": "ana"})
    approve(c, ticket(c, *BILLING)["id"], reasons=["other"])                     # nothing was changed

    stats = c.get("/metrics/breakdown").json()
    assert stats["reasons"] == {"wrong_priority": 1, "tone": 1, "other": 1}      # counted
    assert stats["overrides"] == 3 and stats["reason_coverage"] == 0.67          # 2 of 3 corrections explained
    first = c.get("/tickets", params={"status": "approved"}).json()[0]["review"]
    assert first["note"] == "urgent customer"


# -------------------------------------------------------------------------------------- the admin
def test_us09_the_ticket_system_can_be_connected_safely(monkeypatch):
    triaged = []                                                                 # how often the AI is asked
    real_suggest = main._suggest
    monkeypatch.setattr(main, "_suggest", lambda t: triaged.append(t) or real_suggest(t))
    monkeypatch.setenv("ZAMMAD_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("ZENDESK_WEBHOOK_SECRET", "s3cret")
    zammad, zendesk = FakeZammad(), FakeZendesk()
    zammad.seed(1, tags=["vip"])
    zendesk.seed(2, tags=["renewal"])
    main.systems["zammad"], main.systems["zendesk"] = zammad.make(), zendesk.make()
    c = TestClient(main.app)

    raw = json.dumps(zammad.payload(1, *BILLING, "Sam")).encode()
    sig = "sha1=" + hmac.new(b"s3cret", raw, hashlib.sha1).hexdigest()
    headers = {"content-type": "application/json"}
    assert c.post("/webhooks/zammad", content=raw, headers=headers).status_code == 401
    assert c.post("/webhooks/zammad", content=raw, headers={**headers, "X-Hub-Signature": sig}).status_code == 201

    raw2, ts = json.dumps(zendesk.payload(2, *BILLING, "Ana")).encode(), "2026-10-16T10:00:00Z"
    sig2 = base64.b64encode(hmac.new(b"s3cret", ts.encode() + raw2, hashlib.sha256).digest()).decode()
    zd_headers = {**headers, "X-Zendesk-Webhook-Signature-Timestamp": ts}
    bad = {**zd_headers, "X-Zendesk-Webhook-Signature": "bad"}
    good = {**zd_headers, "X-Zendesk-Webhook-Signature": sig2}
    assert c.post("/webhooks/zendesk", content=raw2, headers=bad).status_code == 401
    assert c.post("/webhooks/zendesk", content=raw2, headers=good).status_code == 201

    assert {"vip", "ai_triaged"} <= zammad.tags(1)                               # the agents' own tags are kept
    assert {"renewal", "ai_triaged"} <= zendesk.tags(2)
    again = c.post("/webhooks/zammad", content=raw, headers={**headers, "X-Hub-Signature": sig}).json()
    assert again["duplicate"] is True and len(zammad.internal(1)) == 1           # a re-sent webhook adds nothing
    assert len(triaged) == 2                                                     # and does not ask the AI again


def test_us10_a_ticket_system_outage_loses_nothing():
    down = ZendeskSystem("acme", "a@acme.com", "tok", transport=httpx.MockTransport(lambda req: httpx.Response(503)))
    main.systems["zendesk"] = down
    c = TestClient(main.app)
    fz = FakeZendesk()
    made = c.post("/webhooks/zendesk", json=fz.payload(9, *BILLING, "Sam"))
    assert made.status_code == 201                                               # still triaged and stored
    tid = made.json()["id"]
    assert c.get(f"/tickets/{tid}").json()["suggestion"]["triage"]["category"] == "billing"

    decided = approve(c, tid, edited_reply="Hi Sam, fixed.")
    assert decided["sync"] == "error"                                            # the call failed...
    assert c.get(f"/tickets/{tid}").json()["review"]["final_reply"] == "Hi Sam, fixed."   # ...the decision did not
    assert [o["mode"] for o in c.get("/outbox").json()] == ["error", "error"]    # and both failures are logged


def test_us11_it_runs_with_no_account_and_no_key(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)                          # any attempt to use the SDK would fail
    fz = FakeZammad()
    c = TestClient(main.app)
    health = c.get("/health").json()
    assert health["llm"] is False and health["ticket_systems"] == {"zendesk": False, "zammad": False}

    made = c.post("/webhooks/zammad", json=fz.payload(1, *BILLING, "Sam")).json()["suggestion"]
    assert made["triage"]["source"] == "rules"                                   # plain rules, and it says so
    assert not made["triage"]["rationale"].startswith("LLM unavailable")         # it did not even try the model
    entry = c.get("/outbox").json()[0]
    assert entry["mode"] == "dry_run" and entry["payload"]["requests"]           # what it would send, not sent
