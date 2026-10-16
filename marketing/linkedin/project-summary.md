# Project summary

**support-copilot** is an independent concept / portfolio project: an add-on for the helpdesk a support team already uses (Zendesk, Zammad or their own): a human-in-the-loop AI copilot for support tickets, built from scratch with synthetic data. It triages each ticket, finds the matching knowledge-base article, drafts a reply in the customer's language and warns when the draft promises something it shouldn't. A person approves every message before it reaches the customer, by tagging the ticket in the helpdesk, and every decision feeds per-category metrics that show where the AI has earned more trust.

- **Positioning:** Creative & Analytical | Product + AI + Automation
- **Stack:** Python, FastAPI, Pydantic, SQLite, Anthropic SDK; a Python library with an optional FastAPI router and an optional plain HTML and JavaScript review page; Zammad and Zendesk adapters
- **Evidence in the repository:** 385 offline tests on Python 3.11 and 3.12 with `ruff` in CI; twelve user stories, each with an acceptance test; the main behaviours replayable with `python scripts/demo.py all`; mutation-style checks documented in `docs/engineering.md`
- **Not claimed:** production readiness, live Zammad, Zendesk or Claude API verification, measured business impact, security guarantees. See `docs/limitations.md`.
- **Companion project:** [agent-triage-kit](https://github.com/monicaslgc/agent-triage-kit), the opposite end of the autonomy spectrum.

Repository: https://github.com/monicaslgc/support-copilot
