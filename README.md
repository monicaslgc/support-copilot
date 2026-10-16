# support-copilot

[![tests](https://github.com/monicaslgc/support-copilot/actions/workflows/tests.yml/badge.svg)](https://github.com/monicaslgc/support-copilot/actions/workflows/tests.yml)

**A human-in-the-loop AI copilot for support tickets.** It triages each ticket, finds the right help article, drafts the reply in the customer's language and warns when a draft promises something it shouldn't. **A person approves every message before it reaches the customer**, and the app measures where the AI has earned more trust.

![The review queue during a simulated outage: four customers report the same problem, the queue flags a possible incident and one click answers them all](docs/incident-spike.png)

*The review queue during a simulated outage (demo data): four customers report the same problem, the queue flags a possible incident, and one click answers them all.*

> **Status: concept / portfolio project**, built from scratch with synthetic data. The Zammad and Zendesk adapters follow the vendors' documented APIs but have only been tested against stateful fakes, and the Claude calls only against a fake SDK. Nothing here has run against a live ticket system or the live API yet. See [what is not verified](docs/limitations.md).

## Why this exists

Support teams want AI help, but cannot afford an AI that sends a customer a wrong promise. And when something breaks, the queue fills with near-identical tickets that all get the same answer, one by one.

This project explores the cautious end of that question: the AI only suggests, a person decides, and every decision is recorded, so you can see per category whether the AI has earned more autonomy. Its companion, [agent-triage-kit](https://github.com/monicaslgc/agent-triage-kit), explores the opposite end: agents that resolve tickets on their own once their confidence clears a threshold.

## Who it is for

- **Support agents** review what the AI prepared and decide what the customer receives. They get triage, a draft, the customer's history and warnings.
- **Support leads** see per category how often the AI is right and why it gets corrected, and decide where it can be trusted with more.
- **Admins** connect Zammad or Zendesk with signed webhooks and without losing anyone's work.

Eleven [user stories](docs/user-stories.md) spell out what each of them needs, with acceptance criteria. Each one is checked by a test, and most by a scenario you can watch run.

## What it does

| Feature | What you get |
|---|---|
| **Triage** | Category, P1-P4 priority, owning team, sentiment and language. Claude when an API key is set, plain rules otherwise. [How](docs/features.md#how-the-ai-part-works-claude-or-rules) |
| **Grounded drafts** | Finds the matching knowledge-base article and drafts a reply in the customer's language, with an apology if they are upset. |
| **Review queue** | Sorted by urgency. Edit the reply, override the triage, approve or reject. The reply that was actually sent is kept. [The page](docs/features.md#the-review-page) |
| **Incidents and duplicates** | Groups tickets about the same issue. Several customers become a possible incident, answered with one click. [Details](docs/features.md#duplicates-and-incident-spikes) |
| **Customer context** | Earlier tickets from the same customer; an upset customer moves a routine ticket up a level. [Details](docs/features.md#sentiment-language-and-customer-context) |
| **Feedback loop** | Records why a reviewer corrected the AI, and reports per category how often it was right, with a confidence bound. [Details](docs/features.md#feedback-why-people-correct-the-ai-and-where-it-has-earned-more-autonomy) |
| **Guardrails** | Masks emails, cards, IBANs, phones and passwords before the model sees them, and flags refund or deadline promises in drafts. [Details](docs/features.md#guardrails-around-the-model) |
| **Ticket systems** | Zammad and Zendesk behind one interface: the AI's notes stay internal, tags are added never replaced, and the public reply only goes out after approval. [Details](docs/integrations.md) |

## How it works

![Architecture: a ticket arrives, private data is masked, it is triaged, a reply is drafted and checked, a person reviews it, and only then the customer is answered; the decision feeds per-category metrics](docs/architecture.png)

## See it

| Customer context | Insights |
|---|---|
| ![Customer context: sentiment, language, earlier tickets and a Portuguese draft with an apology](docs/customer-context.png) | ![Insights: clean rate per category with a confidence bound, and why people corrected the AI](docs/insights.png) |
| Sentiment, language, earlier tickets, and a draft in the customer's language. | Per category, with a confidence bound. These numbers are synthetic demo data. |

| Guardrails | On a phone, in dark mode |
|---|---|
| ![Guardrails: private details masked before the model, and a warning on a reply that promises a refund](docs/guardrails.png) | ![The review page on a phone in dark mode](docs/mobile-dark.png) |
| What was masked, and a warning on a promise (shown with a stub model). | The same page, responsive. |

## Try it

Needs Python 3.11 or newer. No API key and no account are needed.

```bash
pip install -r requirements-dev.txt   # or: make install
uvicorn app.main:app --reload          # review page at http://127.0.0.1:8000/
python scripts/seed_demo.py            # in another terminal: loads sample tickets
python scripts/demo.py all             # plays the user stories against the server and checks each outcome
pytest                                 # 322 tests, offline
```

`scripts/demo.py list` shows the scenarios. [`examples/`](examples/README.md) has ready-made Zammad and Zendesk payloads and the `curl` commands to send them, including signed ones.

To use Claude, set `ANTHROPIC_API_KEY` (the model is `TRIAGE_MODEL`). To connect Zammad or Zendesk, see [integrations](docs/integrations.md). Every setting is in [API and configuration](docs/api.md).

## How it is checked

- **322 tests** on Python 3.11 and 3.12, with `ruff`, in CI. They include the same end-to-end scenarios against both ticket-system fakes, every kind of unusable model reply, races (eight reviews of one ticket, eight identical webhooks) and security checks.
- **User stories are executable:** each of the eleven stories has an acceptance test, and a test fails if a story, its test or its demo scenario drifts apart. The demo scenarios also run in CI, and again and again against one server, as a real one keeps its tickets between runs.
- **Mutation-checked:** I broke the code on purpose in dozens of ways (a swapped public and internal note, overwritten tags, a missing idempotency check, a promise let through) and made sure a test failed each time. Several did not at first, and each of those got a new test.
- **Driven in a real browser** (Chromium) for the approve, reject and bulk flows and a hostile ticket that must show as text. That run is manual, not part of CI.
- **Measured:** saving a ticket stays at about 6.5 ms from 100 to 2000 tickets in an hour. [Engineering notes](docs/engineering.md).

## What it does not do yet

- It has not run against a real Zammad, Zendesk or the live Claude API.
- Retrieval is lexical, and the rule-based fallbacks are baselines, not production classifiers.
- The guardrails are rules with known gaps: they do not mask names or addresses, and an oddly worded promise gets through. A person still reads every reply.
- One instance, one SQLite file. Fine for a few thousand tickets an hour, not more.

Full list and next steps: [limitations](docs/limitations.md).

## Documentation

- [User stories](docs/user-stories.md): who needs what, with acceptance criteria, each checked by a test and most by a runnable scenario
- [Examples](examples/README.md): payloads and `curl` commands for Zammad and Zendesk
- [Features in detail](docs/features.md): the review page, Claude or rules, incidents, customer context, feedback, guardrails
- [Ticket system integrations](docs/integrations.md): Zammad, Zendesk, adding another system
- [API and configuration](docs/api.md): every route and every environment variable
- [Engineering notes](docs/engineering.md): tests and performance
- [Limitations and next steps](docs/limitations.md)

## Built with

Python, FastAPI, Pydantic, SQLite and the Anthropic SDK. The review page is plain HTML and JavaScript with no framework, and never inserts ticket text as HTML. MIT licensed. By [Monica](https://github.com/monicaslgc).
