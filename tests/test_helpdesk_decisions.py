"""A person decides from inside the helpdesk by tagging the ticket, and the tags and names are the company's own.

Run against Zendesk and Zammad fakes with the same assertions: what counts is what the customer and the agents
end up seeing in the ticket system.
"""
import pytest

from support_copilot import main
from support_copilot.config import Taxonomy
from support_copilot.ticketsystems.base import split_tags
from tests.fakes import FakeZammad, FakeZendesk
from tests.test_e2e_flow import Env
from tests.test_robustness import run_threads

SUBJECT, BODY = "Duplicate charge on invoice", "I was charged twice, I want a refund."


@pytest.fixture(params=[FakeZendesk, FakeZammad], ids=["zendesk", "zammad"])
def env(request):
    return Env(request.param())


def arrive(env, tid=100):
    env.fake.seed(tid, tags=["vip"])
    r = env.hook(tid, SUBJECT, BODY)
    assert r.status_code == 201
    return r.json()["id"]


def update(env, tid=100, agent="ana@acme.com", add=()):
    """The agent changes the ticket in the helpdesk; the helpdesk then calls our webhook with its new state."""
    env.fake.agent_adds_tag(tid, *add)
    return env.c.post(f"/webhooks/{env.name}", json=env.fake.payload(tid, SUBJECT, BODY, "Sam", agent, with_tags=True))


def review_row(local_id):
    return main.copilot.fetch_one("SELECT * FROM reviews WHERE ticket_id = ?", (local_id,))


def test_the_note_in_the_ticket_tells_the_agent_how_to_decide(env):
    arrive(env)
    note = env.fake.internal(100)[0]
    assert "add the tag ai_approve" in note and "add the tag ai_reject" in note and "Nothing is sent until then" in note


def test_adding_the_approve_tag_sends_the_drafted_reply_and_records_who_decided(env):
    fz, local_id = env.fake, arrive(env)
    draft = env.c.get(f"/tickets/{local_id}").json()["suggestion"]["draft_reply"]
    assert fz.public(100) == []                                    # nothing before the decision

    r = update(env, add=["ai_approve"])
    assert r.status_code == 201 and r.json()["decided_in"] == env.name and r.json()["sync"] == "sent"
    assert fz.public(100) == [draft]                               # exactly the drafted text, once
    row = review_row(local_id)
    assert (row["action"], row["reviewer"]) == ("approved", "ana@acme.com")
    assert env.c.get(f"/tickets/{local_id}").json()["status"] == "approved"


def test_adding_the_reject_tag_discards_the_draft_and_hands_the_ticket_to_the_agent(env):
    fz, local_id = env.fake, arrive(env)
    update(env, add=["ai_reject"])
    assert fz.public(100) == []
    assert "ai_rejected" in fz.tags(100) and any("rejected by ana@acme.com" in n for n in fz.internal(100))
    assert review_row(local_id)["action"] == "rejected"


def test_updates_without_a_decision_tag_change_nothing(env):
    fz, local_id = env.fake, arrive(env)
    r = update(env, add=["escalated"])
    assert r.json() == {"id": local_id, "duplicate": True}
    assert review_row(local_id) is None and fz.public(100) == []


def test_a_resent_or_echoed_update_does_not_send_the_reply_twice(env):
    fz, local_id = env.fake, arrive(env)
    update(env, add=["ai_approve"])
    again = update(env)                                            # the update our own reply caused, or a retry
    assert again.json()["ignored"] == "already decided"
    assert len(fz.public(100)) == 1 and review_row(local_id) is not None


def test_eight_simultaneous_approve_webhooks_send_exactly_one_reply(env):
    fz, local_id = env.fake, arrive(env)
    fz.agent_adds_tag(100, "ai_approve")
    run_threads(8, lambda i: env.c.post(f"/webhooks/{env.name}", json=fz.payload(
        100, SUBJECT, BODY, "Sam", "ana@acme.com", with_tags=True)))
    assert len(fz.public(100)) == 1
    assert main.copilot.fetch_one("SELECT COUNT(*) AS n FROM reviews WHERE ticket_id = ?", (local_id,))["n"] == 1


def test_both_tags_at_once_are_a_contradiction_and_nothing_happens(env):
    fz, local_id = env.fake, arrive(env)
    r = update(env, add=["ai_approve", "ai_reject"])
    assert r.json()["ignored"] == "both decision tags are set"
    assert fz.public(100) == [] and review_row(local_id) is None


def test_a_new_ticket_that_already_carries_the_tag_is_triaged_not_approved(env):
    env.fake.seed(100, tags=["ai_approve"])
    r = env.c.post(f"/webhooks/{env.name}", json=env.fake.payload(100, SUBJECT, BODY, "Sam", "ana@acme.com", True))
    assert r.json()["status"] == "pending_review" and env.fake.public(100) == []


def test_without_an_agent_in_the_payload_the_reviewer_is_the_system_name(env):
    local_id = arrive(env)
    update(env, agent=None, add=["ai_approve"])
    assert review_row(local_id)["reviewer"] == f"{env.name} agent"


def test_deciding_from_the_helpdesk_can_be_turned_off(env, monkeypatch):
    monkeypatch.setattr(main.copilot, "taxonomy", Taxonomy(helpdesk_decisions=False))
    local_id = arrive(env)
    assert "add the tag" not in env.fake.internal(100)[0]
    update(env, add=["ai_approve"])
    assert env.fake.public(100) == [] and review_row(local_id) is None


def test_only_the_listed_reviewers_can_decide_from_the_helpdesk(env, monkeypatch):
    monkeypatch.setattr(main.copilot, "allowed_reviewers", {"ana@acme.com"})
    local_id = arrive(env)
    assert update(env, agent="intern@acme.com", add=["ai_approve"]).json()["ignored"].startswith("reviewer is not")
    assert update(env, agent=None).json()["ignored"].startswith("reviewer is not")   # unknown person: not allowed
    assert env.fake.public(100) == [] and review_row(local_id) is None
    assert update(env, agent="ANA@acme.com").status_code == 201                      # case does not matter
    assert len(env.fake.public(100)) == 1


def test_the_company_names_and_tags_are_used_everywhere(env, monkeypatch):
    monkeypatch.setattr(main.copilot, "taxonomy", Taxonomy(
        tag_prefix="copilot_", category_labels={"billing": "finance-queries"}, team_labels={"finance": "accounts"},
        approve_tag="send", reject_tag="discard"))
    local_id = arrive(env)
    fz = env.fake
    assert {"vip", "copilot_triaged", "copilot_cat_finance-queries", "copilot_team_accounts"} <= fz.tags(100)
    assert not any(t.startswith("ai_") for t in fz.tags(100))
    note = fz.internal(100)[0]
    assert "Category: finance-queries" in note and "Team: accounts" in note and "add the tag copilot_send" in note

    assert update(env, add=["ai_approve"]).json() == {"id": local_id, "duplicate": True}   # the old tag is nothing
    update(env, add=["copilot_discard"])
    assert "copilot_rejected" in fz.tags(100) and fz.public(100) == []


def test_a_taxonomy_that_would_write_invalid_tags_is_refused_up_front():
    for bad in (dict(tag_prefix="AI "), dict(approve_tag="Ok Go"), dict(category_labels={"bug": "Has Space"}),
                dict(approve_tag="x", reject_tag="x")):
        with pytest.raises(ValueError):
            Taxonomy(**bad)
    assert Taxonomy().approve == "ai_approve" and Taxonomy(tag_prefix="co-").reject == "co-reject"


def test_tags_arrive_as_a_list_or_as_the_string_a_template_renders():
    assert split_tags("VIP  ai_approve,renewal") == ("vip", "ai_approve", "renewal")
    assert split_tags(["VIP", " ai_reject "]) == ("vip", "ai_reject") and split_tags(None) == split_tags("") == ()


def test_decision_tags_match_whatever_case_an_adapter_hands_over():
    from support_copilot.models import Decision
    assert Taxonomy().decision(["VIP", " AI_Approve "]) == (Decision.APPROVED, False)
    assert Taxonomy().decision(["ai_reject"]) == (Decision.REJECTED, False)
    assert Taxonomy().decision(["ai_approve", "AI_REJECT"]) == (None, True)
    assert Taxonomy().decision([]) == (None, False)
