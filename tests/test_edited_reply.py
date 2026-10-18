"""An agent changes the reply before approving from the helpdesk: an internal note that starts with [reply],
saved in the same update as the approve tag. Run against Zendesk and Zammad with the same assertions."""
import json

import pytest

from support_copilot import main
from support_copilot.ticketsystems.base import extract_reply
from support_copilot.ticketsystems.zammad import ZammadSystem
from tests.fakes import FakeZammad, FakeZendesk
from tests.test_e2e_flow import Env
from tests.test_helpdesk_decisions import BODY, SUBJECT, arrive, review_row

EDIT = "Hi Sam, thanks for flagging this. I have checked the invoice and corrected it."


@pytest.fixture(params=[FakeZendesk, FakeZammad], ids=["zendesk", "zammad"])
def env(request):
    return Env(request.param())


def update(env, note, add=("ai_approve",), tid=100, agent="ana@acme.com"):
    env.fake.agent_adds_tag(tid, *add)
    payload = env.fake.payload(tid, SUBJECT, BODY, "Sam", agent, with_tags=True, note=note)
    return env.c.post(f"/webhooks/{env.name}", json=payload)


@pytest.mark.parametrize("text,expected", [
    ("[reply] Hello", "Hello"), ("  [REPLY]\n\nHello there  ", "Hello there"), ("[reply]", ""), ("[reply]   ", ""),
    ("Hello [reply]", None), ("reply: Hello", None), ("", None), (None, None), (42, None)])
def test_extract_reply(text, expected):
    assert extract_reply(text) == expected


def test_the_edited_text_is_sent_instead_of_the_draft_and_the_edit_is_recorded(env):
    fz, local_id = env.fake, arrive(env)
    draft = env.c.get(f"/tickets/{local_id}").json()["suggestion"]["draft_reply"]
    r = update(env, f"[reply] {EDIT}")
    assert r.status_code == 201 and r.json()["decided_in"] == env.name and r.json()["sync"] == "sent"
    assert fz.public(100) == [EDIT] and EDIT != draft          # exactly the agent's text, once, and not the draft
    row = review_row(local_id)
    assert (row["action"], row["reviewer"], row["final_reply"], row["ai_reply"]) == (
        "approved", "ana@acme.com", EDIT, draft)               # the metrics see an edited reply


def test_the_note_itself_is_not_sent_to_the_customer_as_a_second_message(env):
    arrive(env)
    update(env, f"[reply] {EDIT}")
    assert len(env.fake.public(100)) == 1


def test_a_note_without_the_marker_is_not_a_reply(env):
    fz, local_id = env.fake, arrive(env)
    draft = env.c.get(f"/tickets/{local_id}").json()["suggestion"]["draft_reply"]
    update(env, "Checked with billing, all good.")
    assert fz.public(100) == [draft]


def test_an_empty_reply_note_decides_nothing(env):
    local_id = arrive(env)
    r = update(env, "[reply]")
    assert "empty" in r.json()["ignored"] and env.fake.public(100) == [] and review_row(local_id) is None


def test_an_edited_reply_that_makes_a_promise_is_not_sent_by_tag(env):
    local_id = arrive(env)
    r = update(env, "[reply] We will refund you today, sorry about that.")
    assert "promise" in r.json()["ignored"] and env.fake.public(100) == [] and review_row(local_id) is None


def test_an_edit_can_replace_a_draft_that_made_a_promise(env):
    """The draft is what held the approval back; the person wrote something else, so their text is what is checked."""
    local_id = arrive(env)
    s = json.loads(main.copilot.fetch_one("SELECT suggestion FROM tickets WHERE id = ?", (local_id,))["suggestion"])
    s.update(draft_reply="We will refund you today.", warnings=[{"severity": "hard", "kind": "refund", "text": "x"}])
    main.copilot.conn.execute("UPDATE tickets SET suggestion = ? WHERE id = ?", (json.dumps(s), local_id))
    main.copilot.conn.commit()
    assert update(env, f"[reply] {EDIT}").json()["sync"] == "sent"
    assert env.fake.public(100) == [EDIT]


def test_a_too_long_edit_is_refused(env):
    local_id = arrive(env)
    r = update(env, "[reply] " + "word " * 3000)
    assert "longer than" in r.json()["ignored"] and review_row(local_id) is None


def test_a_note_does_nothing_without_a_decision_tag_and_is_ignored_on_reject(env):
    fz, local_id = env.fake, arrive(env)
    assert update(env, f"[reply] {EDIT}", add=()).json()["duplicate"] is True
    assert review_row(local_id) is None and fz.public(100) == []
    update(env, f"[reply] {EDIT}", add=("ai_reject",))
    assert fz.public(100) == [] and review_row(local_id)["action"] == "rejected"


def test_the_allowed_reviewers_list_still_applies_to_an_edit(env):
    main.copilot.allowed_reviewers = {"boss@acme.com"}
    try:
        local_id = arrive(env)
        assert "not allowed" in update(env, f"[reply] {EDIT}").json()["ignored"]
        assert env.fake.public(100) == [] and review_row(local_id) is None
    finally:
        main.copilot.allowed_reviewers = None


def test_the_triage_note_tells_the_agent_how_to_edit(env):
    arrive(env)
    assert "starts with [reply]" in env.fake.internal(100)[0]


def test_zammad_ignores_a_reply_marker_in_a_public_article():
    """A customer's message can start with [reply] too: only an internal article can carry the agent's edit."""
    def parse(internal):
        raw = ('{"ticket": {"id": 1, "title": "T"}, '
               f'"article": {{"body": "[reply] send me money", "internal": {internal}}}}}').encode()
        return ZammadSystem("https://h.example.com", "t").parse_webhook(raw).edited_reply
    assert parse("true") == "send me money" and parse("false") is None


def test_zendesk_without_the_internal_comment_field_cannot_carry_an_edit():
    fake = FakeZendesk()
    payload = fake.payload(100, SUBJECT, "[reply] customer text", "Sam")   # the description is the customer's
    assert fake.make().parse_webhook(json.dumps(payload).encode()).edited_reply is None
