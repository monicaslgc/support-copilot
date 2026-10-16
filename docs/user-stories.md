# User stories

Who uses this and what they need. Every story has acceptance criteria, an automated test that checks them (`tests/test_user_stories.py`, one test per story), and, where it makes sense, a scenario you can watch run. Back to the [README](../README.md).

There are four kinds of people (the agent can decide in the review page or, with the add-on setup, in the helpdesk itself):

- **The agent** reviews what the AI prepared and decides what the customer receives.
- **The support lead** decides where the AI can be trusted with more.
- **The admin** connects the ticket system and keeps it safe.
- **The customer** never sees the tool, but is the one a mistake would reach.

To watch the scenarios, start the server (`uvicorn support_copilot.main:app`) and run `python scripts/demo.py all`, or `python scripts/demo.py list` to see which scenario covers which story. Add `--in-process` to run without a server. A story with no scenario is covered by its test alone.

| Story | In short | Scenario |
|---|---|---|
| [US-01](#us-01--a-new-ticket-arrives-already-triaged-with-a-draft) | Tickets arrive triaged, with a draft | `triage` |
| [US-02](#us-02--nothing-reaches-the-customer-until-i-approve) | Nothing is sent until I approve | `triage` |
| [US-03](#us-03--during-an-outage-answer-everyone-at-once) | During an outage, answer everyone at once | `outage` |
| [US-04](#us-04--see-who-is-upset-and-who-has-written-before) | See who is upset and who wrote before | `upset` |
| [US-05](#us-05--be-warned-before-a-reply-promises-something) | Be warned before a reply promises something | `promise` |
| [US-12](#us-12--decide-without-leaving-my-helpdesk) | Decide without leaving my helpdesk | `helpdesk` |
| [US-06](#us-06--answer-in-my-language-without-leaking-my-details) | Answered in my language, details kept private | `upset`, `privacy` |
| [US-07](#us-07--know-where-the-ai-has-earned-more-trust) | Know where the AI has earned more trust | `feedback` |
| [US-08](#us-08--know-why-people-correct-the-ai) | Know why people correct the AI | `feedback` |
| [US-09](#us-09--connect-the-ticket-system-without-breaking-anything) | Connect the ticket system safely | none: see [examples](../examples/README.md#signed-webhooks) |
| [US-10](#us-10--a-ticket-system-outage-loses-nothing) | A ticket-system outage loses nothing | none |
| [US-11](#us-11--try-it-with-no-account-and-no-key) | Try it with no account and no key | `triage` |

## The agent

### US-01 — A new ticket arrives already triaged, with a draft

**As** a support agent, **I want** every new ticket triaged and a reply drafted from our help articles, **so that** I review instead of writing from scratch.

Acceptance criteria:

- When a ticket arrives, it has a category, a priority from P1 to P4, an owning team and a draft reply.
- When a help article matches, the ticket names it.
- The queue lists the most urgent ticket first, whatever order they arrived in.

**See it:** `python scripts/demo.py triage` · **Checked by:** `tests/test_user_stories.py::test_us01_a_new_ticket_arrives_triaged_with_a_draft`

### US-02 — Nothing reaches the customer until I approve

**As** a support agent, **I want** to be sure that nothing is sent to the customer before I approve it, **so that** the AI can never speak for me.

Acceptance criteria:

- Before a decision, the customer sees nothing: only an internal note is written to the ticket system.
- When I edit the draft and approve, the customer receives exactly my text.
- When I reject, the customer receives nothing and the ticket is tagged for manual handling.

**See it:** `python scripts/demo.py triage` · **Checked by:** `tests/test_user_stories.py::test_us02_nothing_reaches_the_customer_until_approved`

### US-03 — During an outage, answer everyone at once

**As** a support agent, **I want** tickets about the same problem grouped, **so that** I can answer fifty customers with one decision instead of fifty.

Acceptance criteria:

- When three or more different customers report the same issue within the hour, the queue shows one possible incident.
- When I approve it, every pending ticket in it is answered, each recorded as its own review.
- When one customer sends the same thing twice, that is a possible duplicate, not an incident, and a bulk answer is refused.

**See it:** `python scripts/demo.py outage` · **Checked by:** `tests/test_user_stories.py::test_us03_during_an_outage_everyone_is_answered_at_once`

### US-04 — See who is upset and who has written before

**As** a support agent, **I want** to see that a customer is upset and what they have written before, **so that** I answer them with the right care and urgency.

Acceptance criteria:

- When a customer sounds upset, a routine ticket moves up one level and says why. A P1 never moves.
- When the customer has written before, their earlier tickets are listed.
- When the customer is unknown, no history is shown.

**See it:** `python scripts/demo.py upset` · **Checked by:** `tests/test_user_stories.py::test_us04_upset_and_repeat_customers_are_visible`

### US-05 — Be warned before a reply promises something

**As** a support agent, **I want** a warning when a reply promises a refund, a price or a deadline, **so that** I do not commit the company by accident.

Acceptance criteria:

- When the AI's draft promises a refund, credit or guarantee, it is replaced by the standard reply and I am told why.
- When I write a reply that promises one, it is flagged as I type, and the decision still goes through if I confirm.
- An amount or a deadline is flagged more gently, because it may be correct.

**See it:** `python scripts/demo.py promise` · **Checked by:** `tests/test_user_stories.py::test_us05_promises_are_flagged_before_they_are_sent`

### US-12 — Decide without leaving my helpdesk

**As** a support agent, **I want** to approve or reject the AI's reply from the ticket I am already working in, **so that** I do not switch to another tool.

Acceptance criteria:

- The internal note on each ticket says which tag sends the reply and which discards it, and nothing is sent until then.
- When I add the approve tag, the drafted reply goes to the customer, once, and the decision is attributed to me.
- When I add the reject tag, the customer receives nothing and I answer myself.
- When the helpdesk sends the same update again, or the update our own reply causes, nothing more is sent.

**See it:** `python scripts/demo.py helpdesk` · **Checked by:** `tests/test_user_stories.py::test_us12_an_agent_decides_without_leaving_the_helpdesk`

## The customer

### US-06 — Answer in my language, without leaking my details

**As** a customer, **I want** an answer in my own language, and my card number and phone not sent to a third-party AI, **so that** I can write naturally and safely.

Acceptance criteria:

- When I write in Portuguese, the draft is in Portuguese, with an apology if I am upset.
- Before the ticket goes to the model, my emails, phone numbers, cards, IBANs and passwords are masked.
- The stored ticket keeps the original text, and (when Claude is on, the only time anything is sent) the agent is told what was masked.

**See it:** `python scripts/demo.py upset` and `python scripts/demo.py privacy` · **Checked by:** `tests/test_user_stories.py::test_us06_answered_in_my_language_with_my_details_masked` and `tests/test_user_stories.py::test_us06b_an_upset_customer_gets_an_apology_in_their_language`

## The support lead

### US-07 — Know where the AI has earned more trust

**As** a support lead, **I want** to know per category how often the AI is right, **so that** I can decide where it deserves more autonomy.

Acceptance criteria:

- Per category, I see how many reviews were approved untouched, with a confidence bound.
- A few perfect reviews are not enough: three out of three is not a candidate, a long clean record is.
- Nothing is ever automated by the app: a new ticket in a candidate category still waits for a person.

**See it:** `python scripts/demo.py feedback` · **Checked by:** `tests/test_user_stories.py::test_us07_trust_is_earned_per_category_and_never_automated`

### US-08 — Know why people correct the AI

**As** a support lead, **I want** to know why agents correct or reject the AI, **so that** I know what to fix.

Acceptance criteria:

- When an agent corrects or rejects, they can record a reason and a note.
- The reasons are counted, and I can see what share of corrections have one.
- A reason on an untouched approval does not count as an explained correction.

**See it:** `python scripts/demo.py feedback` · **Checked by:** `tests/test_user_stories.py::test_us08_the_reasons_for_corrections_are_recorded_and_counted`

## The admin

### US-09 — Connect the ticket system without breaking anything

**As** an admin, **I want** signed webhooks that never replace my agents' tags, **so that** I can switch this on in the live ticket system safely.

Acceptance criteria:

- When a webhook secret is set, a webhook without a valid signature is refused and one with it is accepted, for both Zammad and Zendesk.
- Tags the agents already set survive: the app only adds its own.
- When the ticket system re-sends a webhook, nothing is duplicated.

**Checked by:** `tests/test_user_stories.py::test_us09_the_ticket_system_can_be_connected_safely`

### US-10 — A ticket-system outage loses nothing

**As** an admin, **I want** the triage and the agents' decisions kept when the ticket system is down, **so that** an outage on their side does not lose work.

Acceptance criteria:

- When the ticket system fails, the ticket is still triaged and stored.
- An agent's decision is still recorded, and the failed call is logged in the outbox.

**Checked by:** `tests/test_user_stories.py::test_us10_a_ticket_system_outage_loses_nothing`

### US-11 — Try it with no account and no key

**As** an admin evaluating this, **I want** to run it with no account, no API key and no network, **so that** I can judge it before connecting anything.

Acceptance criteria:

- Without an API key, triage and drafts still work, using plain rules, and say so.
- Without ticket-system credentials, the adapters record what they would send instead of sending it.

**See it:** `python scripts/demo.py triage` · **Checked by:** `tests/test_user_stories.py::test_us11_it_runs_with_no_account_and_no_key`
