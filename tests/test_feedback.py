"""Why humans correct the AI, and what that says per category."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.feedback import breakdown, wilson_lower

BILLING = ("Duplicate charge", "I was charged twice, I want a refund for this invoice")     # AI: billing / P3
BUG = ("Save fails", "I get an error and the page crashes when saving")                      # AI: bug / P2


def make(c, text):
    return c.post("/tickets", json={"subject": text[0], "body": text[1]}).json()["id"]


def decide(c, tid, action="approved", **body):
    r = c.post(f"/tickets/{tid}/review", json={"action": action, "reviewer": "monica", **body})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------- the maths ----------------
@pytest.mark.parametrize("successes,n,expected", [(0, 0, 0.0), (3, 3, 0.4385), (20, 20, 0.8389),
                                                  (34, 34, 0.8985), (35, 35, 0.9011), (100, 100, 0.9630),
                                                  (10, 20, 0.2993), (0, 10, 0.0)])
def test_wilson_lower_bound(successes, n, expected):
    assert wilson_lower(successes, n) == pytest.approx(expected, abs=1e-3)


def test_a_perfect_record_on_few_reviews_is_not_trusted():
    assert wilson_lower(3, 3) < 0.5 < wilson_lower(30, 30)


# ---------------- storing the reasons ----------------
def test_reasons_and_note_are_stored_and_shown_with_the_ticket():
    c = TestClient(main.app)
    tid = make(c, BILLING)
    decide(c, tid, edited_reply="Hi, refunded.", reasons=["tone", "missing_info", "tone"], note="Too formal")
    review = c.get(f"/tickets/{tid}").json()["review"]
    assert review["reasons"] == ["tone", "missing_info"]                  # repeats removed, order kept
    assert review["note"] == "Too formal"


def test_a_review_without_reasons_is_fine():
    c = TestClient(main.app)
    tid = make(c, BILLING)
    decide(c, tid)
    assert c.get(f"/tickets/{tid}").json()["review"]["reasons"] == []


@pytest.mark.parametrize("body", [{"reasons": ["because"]}, {"reasons": "tone"}, {"note": "x" * 501},
                                  {"reasons": ["tone"] * 3 + ["other"] * 6 + ["bogus"]}])
def test_invalid_feedback_is_rejected_and_the_ticket_stays_pending(body):
    c = TestClient(main.app)
    tid = make(c, BILLING)
    r = c.post(f"/tickets/{tid}/review", json={"action": "rejected", "reviewer": "m", **body})
    assert r.status_code == 422
    assert c.get(f"/tickets/{tid}").json()["status"] == "pending_review"


def test_a_bulk_decision_records_the_reasons_on_every_ticket():
    c = TestClient(main.app)
    ids = [c.post("/tickets", json={"subject": s, "body": b, "customer": who}).json()["id"]
           for (s, b), who in zip([("Platform is down", "Nobody can access anything, total outage since 9am."),
                                   ("Cannot reach the platform", "The platform is unavailable, is it down?"),
                                   ("Service unavailable", "The platform is slow and unavailable, outage?")],
                                  ("Ana", "Rui", "Sam"), strict=True)]
    cl = c.get("/clusters").json()[0]
    c.post(f"/clusters/{cl['id']}/review", json={"action": "rejected", "reviewer": "m",
                                                 "reasons": ["duplicate"], "note": "known incident"})
    for tid in ids:
        r = c.get(f"/tickets/{tid}").json()["review"]
        assert r["reasons"] == ["duplicate"] and r["note"] == "known incident"


# ---------------- the breakdown ----------------
def build_dataset(c):
    b = [make(c, BILLING) for _ in range(6)]
    decide(c, b[0])
    decide(c, b[1])
    decide(c, b[2], final_priority="P2", reasons=["wrong_priority"])
    decide(c, b[3], "rejected", reasons=["duplicate"], note="same as the first")
    decide(c, b[4], edited_reply="Hi, refunded.", reasons=["tone", "missing_info"])
    decide(c, b[5], final_category="bug", reasons=["wrong_category"])
    g = [make(c, BUG) for _ in range(2)]
    decide(c, g[0])
    decide(c, g[1], "rejected")                                          # a rejection nobody explained


def test_breakdown_numbers_per_category():
    c = TestClient(main.app)
    build_dataset(c)
    m = c.get("/metrics/breakdown").json()

    assert (m["reviewed"], m["rejected"], m["overrides"]) == (8, 2, 5)
    assert m["reason_coverage"] == 0.8                                    # 4 of the 5 overrides were explained
    assert m["reasons"] == {"wrong_priority": 1, "duplicate": 1, "tone": 1, "missing_info": 1, "wrong_category": 1}

    billing, bug = m["by_category"]                                       # most reviewed first
    assert billing == {
        "category": "billing", "reviewed": 6, "approved": 5, "rejected": 1, "rejection_rate": 0.17,
        "category_agreement": 0.8, "priority_agreement": 0.8, "reply_edit_rate": 0.2,
        "clean_rate": 0.33, "clean_rate_lower_bound": round(wilson_lower(2, 6), 2), "automation_candidate": False}
    assert bug == {
        "category": "bug", "reviewed": 2, "approved": 1, "rejected": 1, "rejection_rate": 0.5,
        "category_agreement": 1.0, "priority_agreement": 1.0, "reply_edit_rate": 0.0,
        "clean_rate": 0.5, "clean_rate_lower_bound": round(wilson_lower(1, 2), 2), "automation_candidate": False}
    assert "nothing is automated" in m["automation"]["note"]


def test_the_headline_metrics_are_unchanged():
    c = TestClient(main.app)
    build_dataset(c)
    assert c.get("/metrics").json() == {"reviewed": 6, "category_agreement": 0.83,
                                        "priority_agreement": 0.83, "reply_edit_rate": 0.17}


def test_no_reviews_yet():
    m = TestClient(main.app).get("/metrics/breakdown").json()
    assert m["reviewed"] == 0 and m["by_category"] == [] and m["reason_coverage"] is None and m["reasons"] == {}


def clean_reviews(c, n):
    for _ in range(n):
        decide(c, make(c, BILLING))


def test_a_category_becomes_a_candidate_only_after_enough_clean_reviews():
    c = TestClient(main.app)
    clean_reviews(c, 34)
    assert c.get("/metrics/breakdown").json()["by_category"][0]["automation_candidate"] is False   # lower bound 0.8985
    clean_reviews(c, 1)
    top = c.get("/metrics/breakdown").json()["by_category"][0]
    assert top["automation_candidate"] is True and top["clean_rate"] == 1.0


def test_a_single_correction_delays_candidacy_until_the_record_grows():
    c = TestClient(main.app)
    clean_reviews(c, 39)
    decide(c, make(c, BILLING), final_priority="P1")
    assert c.get("/metrics/breakdown").json()["by_category"][0]["automation_candidate"] is False     # 39/40 -> 0.87
    clean_reviews(c, 60)
    assert c.get("/metrics/breakdown").json()["by_category"][0]["automation_candidate"] is True      # 99/100 -> 0.95


def test_the_candidate_thresholds_are_configurable(monkeypatch):
    c = TestClient(main.app)
    clean_reviews(c, 35)
    monkeypatch.setenv("AUTOMATION_MIN_REVIEWS", "50")
    assert c.get("/metrics/breakdown").json()["by_category"][0]["automation_candidate"] is False
    monkeypatch.setenv("AUTOMATION_MIN_REVIEWS", "20")
    monkeypatch.setenv("AUTOMATION_MIN_LOWER_BOUND", "0.95")
    assert c.get("/metrics/breakdown").json()["by_category"][0]["automation_candidate"] is False
    monkeypatch.setenv("AUTOMATION_MIN_LOWER_BOUND", "0.8")
    assert c.get("/metrics/breakdown").json()["by_category"][0]["automation_candidate"] is True


def test_a_rejection_counts_against_clean_even_though_it_was_not_approved():
    rows = [{"action": "rejected", "ai_category": "bug", "ai_priority": "P2", "ai_reply": "a",
             "final_category": "bug", "final_priority": "P2", "final_reply": "a", "reasons": None}]
    b = breakdown(rows)["by_category"][0]
    assert b["clean_rate"] == 0.0 and b["rejection_rate"] == 1.0 and b["category_agreement"] is None


def test_breakdown_needs_the_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    c = TestClient(main.app)
    assert c.get("/metrics/breakdown").status_code == 401
    assert c.get("/metrics/breakdown", headers={"X-API-Key": "k"}).status_code == 200


def test_a_reason_on_an_untouched_approval_does_not_count_as_an_explained_correction():
    base = {"ai_category": "bug", "ai_priority": "P2", "ai_reply": "a",
            "final_category": "bug", "final_priority": "P2", "final_reply": "a"}
    rows = [{**base, "action": "approved", "reasons": '["other"]'},                    # nothing was changed
            {**base, "action": "rejected", "reasons": None}]                           # a real correction, unexplained
    m = breakdown(rows)
    assert m["overrides"] == 1 and m["reason_coverage"] == 0.0
