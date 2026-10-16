# support-copilot

[![tests](https://github.com/monicaslgc/support-copilot/actions/workflows/tests.yml/badge.svg)](https://github.com/monicaslgc/support-copilot/actions/workflows/tests.yml) ![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-153D32) ![MIT licence](https://img.shields.io/badge/licence-MIT-A0B3A0) ![Status: concept](https://img.shields.io/badge/status-concept%20%2F%20portfolio-202A27)

![support-copilot: AI-assisted support, human-approved decisions. A review page with the AI's draft and a separate human decision panel.](docs/social-preview.png)

**A human-in-the-loop AI copilot for support tickets.** It triages each ticket, finds the right help article, drafts the reply in the customer's language and warns when a draft promises something it shouldn't. **A person approves every message before it reaches the customer**, and the app measures where the AI has earned more trust.

[Try it](#try-it) · [See it](#see-it-in-action) · [How it works](#how-it-works) · [What is verified](#what-is-verified) · [Docs](#documentation)

> **Status: concept / portfolio project**, built from scratch with synthetic data. The Zammad and Zendesk adapters follow the vendors' documented APIs but have only been tested against stateful fakes, and the Claude calls only against a fake SDK. Nothing here has run against a live ticket system or the live API yet. See [what is not verified](docs/limitations.md).

## Why this exists

Support teams want AI help, but cannot afford an AI that sends a customer a wrong promise. And when something breaks, the queue fills with near-identical tickets that all get the same answer, one by one.

This project explores the cautious end of that question: **the AI only suggests, a person decides, and every decision is recorded**, so you can see per category whether the AI has earned more autonomy. Its companion, [agent-triage-kit](https://github.com/monicaslgc/agent-triage-kit), explores the opposite end: agents that resolve tickets on their own once their confidence clears a threshold.

## The human-in-the-loop principle

- **The AI prepares:** triage, a grounded draft, related tickets and warnings.
- **A person decides:** edits the reply, overrides the triage, approves or rejects, and can say why. Nothing is sent before that.
- **Deterministic checks sit around the model:** private data is masked before it, and promises are caught after it, with plain rules that can be read and tested.
- **Every decision is data:** per category, how often the AI was right, with a confidence bound, and why it was corrected.

## What it does

| Capability | What you get |
|---|---|
| **Triage** | Category, P1-P4 priority, owning team, sentiment and language. Claude when an API key is set, plain rules otherwise. [How](docs/features.md#how-the-ai-part-works-claude-or-rules) |
| **Grounded drafts** | Finds the matching knowledge-base article and drafts a reply in the customer's language, with an apology if they are upset. |
| **Review queue** | Sorted by urgency. Edit the reply, override the triage, approve or reject. The reply that was actually sent is kept. [The page](docs/features.md#the-review-page) |
| **Incidents and duplicates** | Groups tickets about the same issue. Several customers become a possible incident, answered with one click. [Details](docs/features.md#duplicates-and-incident-spikes) |
| **Customer context** | Earlier tickets from the same customer; an upset customer moves a routine ticket up a level. [Details](docs/features.md#sentiment-language-and-customer-context) |
| **Feedback loop** | Records why a reviewer corrected the AI, and reports per category how often it was right. [Details](docs/features.md#feedback-why-people-correct-the-ai-and-where-it-has-earned-more-autonomy) |
| **Guardrails** | Masks emails, cards, IBANs, phones and passwords before the model sees them, and flags refund or deadline promises in drafts. [Details](docs/features.md#guardrails-around-the-model) |
| **Ticket systems** | Zammad and Zendesk behind one interface: the AI's notes stay internal, tags are added never replaced, and the public reply only goes out after approval. [Details](docs/integrations.md) |

Eleven [user stories](docs/user-stories.md) (support agents, support leads, admins) spell out what each role needs. Each one is checked by a test, and most by a scenario you can watch run.

## See it in action

<p align="center"><img src="docs/review-queue.png" width="760" alt="The review page: the customer's ticket, the AI's triage and draft in sage panels, and a separate bordered Your decision panel with Approve and Reject."></p>
<p align="center"><em>The review page (synthetic demo data). Ticket, AI suggestion and human decision are three visibly different things.</em></p>

| An outage becomes one incident | A promise gets a warning |
|---|---|
| <img src="docs/incident-spike.png" alt="Four P1 tickets grouped as a possible incident, with an Approve for all 4 pending button."> | <img src="docs/guardrails.png" alt="Private details masked before the model, and a warning on a reply that promises a refund."> |
| Four customers report the same problem; one click answers them all. | What was masked, and a warning on a refund promise (shown with a stub model). |

| Customer context | Where the AI has earned trust |
|---|---|
| <img src="docs/customer-context.png" alt="An upset Portuguese-speaking customer with earlier tickets and a Portuguese draft."> | <img src="docs/insights.png" alt="Clean rate per category with a confidence lower bound, and the reasons people corrected the AI."> |
| Sentiment, language, earlier tickets, and a draft in the customer's language. | Per category, with a confidence bound. Synthetic demo reviews. |

<img src="docs/mobile-dark.png" width="300" align="right" alt="The review page on a phone in dark mode, ending with the decision panel."> The page is responsive, supports dark mode and keyboard use, and keeps the decision panel at the end of every ticket, on a phone too. Colour is never the only signal: approved and rejected tickets carry a written status.

<br clear="right">

## How it works

![Architecture: a ticket arrives, private data is masked, it is triaged, a reply is drafted and checked, a person reviews it, and only then the customer is answered; the decision feeds per-category metrics](docs/architecture.png)

## Responsible AI decisions

- **Masking before the model.** Emails, phones, cards (Luhn-checked), IBANs (mod-97-checked) and passwords become `[EMAIL]`, `[CARD]` and so on. The stored ticket keeps the original, since the reviewer needs it.
- **Promises are caught after it.** A model-written draft with a refund, guarantee or free offer is replaced by the standard reply; if a reviewer types one, the banner turns red and approval needs a second click.
- **Failure is visible and safe.** A refusal, truncation, invalid JSON or API outage falls back to the rules, and the triage says so (`source: rules`). A failed ticket-system call is logged in the outbox, not lost.
- **Known gaps, stated.** Names and postal addresses are not masked, an oddly worded promise gets through, and a reviewer can click through the warning. A person still reads every reply. [Guardrails in detail](docs/features.md#guardrails-around-the-model)

## How it is validated

- **332 tests** on Python 3.11 and 3.12, with `ruff`, in CI. They include the same end-to-end scenarios against both ticket-system fakes, every kind of unusable model reply, races (eight reviews of one ticket, eight identical webhooks), security checks and the review page's brand tokens, contrast ratios and approval controls.
- **User stories are executable:** each of the eleven stories has an acceptance test, and a test fails if a story, its test or its demo scenario drifts apart.
- **Mutation-checked:** I broke the code on purpose in dozens of ways (a swapped public and internal note, overwritten tags, a missing idempotency check, a promise let through) and made sure a test failed each time. Several did not at first, and each of those got a new test.
- **Driven in a real browser** (Chromium) for the approve, reject and bulk flows and a hostile ticket that must show as text. That run is manual, not part of CI.
- **Measured:** saving a ticket stays at about 6.5 ms from 100 to 2000 tickets in an hour. [Engineering notes](docs/engineering.md).

## What is verified

| Area | State |
|---|---|
| Triage, drafts, review page, guardrails, feedback metrics | **Implemented and tested offline** (332 tests, synthetic data) |
| Zammad and Zendesk adapters | **Prototyped:** tested against stateful fakes only, never a live instance |
| Claude calls | **Prototyped:** tested against a fake SDK only, never the live API |
| Retrieval and rule-based fallbacks | **Baselines:** lexical retrieval and keyword rules, not production classifiers |
| Guardrails | **Rules with known gaps:** names and addresses are not masked, an oddly worded promise gets through |
| Scale and auth | One instance, one SQLite file, one shared API key. A few thousand tickets an hour at most |

Full list and next steps: [limitations](docs/limitations.md).

## Try it

Needs Python 3.11 or newer. No API key and no account are needed.

```bash
pip install -r requirements-dev.txt   # or: make install
uvicorn app.main:app --reload          # review page at http://127.0.0.1:8000/
python scripts/seed_demo.py            # in another terminal: loads sample tickets
python scripts/demo.py all             # plays the user stories against the server and checks each outcome
pytest                                 # 332 tests, offline
```

`scripts/demo.py list` shows the scenarios. [`examples/`](examples/README.md) has ready-made Zammad and Zendesk payloads and the `curl` commands to send them, including signed ones. To use Claude, set `ANTHROPIC_API_KEY` (the model is `TRIAGE_MODEL`). To connect Zammad or Zendesk, see [integrations](docs/integrations.md). Every setting is in [API and configuration](docs/api.md).

## Documentation

- [User stories](docs/user-stories.md): who needs what, with acceptance criteria, each checked by a test
- [Examples](examples/README.md): payloads and `curl` commands for Zammad and Zendesk
- [Features in detail](docs/features.md): the review page, Claude or rules, incidents, customer context, feedback, guardrails
- [Ticket system integrations](docs/integrations.md): Zammad, Zendesk, adding another system
- [API and configuration](docs/api.md): every route and every environment variable
- [Engineering notes](docs/engineering.md): tests and performance
- [Limitations and next steps](docs/limitations.md)
- [Visual identity and assets](docs/presentation.md): brand tokens, regenerating screenshots, social preview

## Built with

Python, FastAPI, Pydantic, SQLite and the Anthropic SDK. The review page is plain HTML and JavaScript with no framework, and never inserts ticket text as HTML. MIT licensed. By [Monica](https://github.com/monicaslgc).
