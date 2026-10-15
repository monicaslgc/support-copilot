# support-copilot
[![tests](https://github.com/monicaslgc/support-copilot/actions/workflows/tests.yml/badge.svg)](https://github.com/monicaslgc/support-copilot/actions/workflows/tests.yml)

A human-in-the-loop AI copilot for support tickets. It triages incoming tickets (category, P1-P4 priority, owning team), finds relevant knowledge-base articles (RAG) and drafts a reply. **Nothing reaches the customer until a human approves it.**

This is a portfolio/concept project, built from scratch with synthetic data. The Zendesk integration is a **concept**: it follows the Zendesk webhook and Support API shapes, but it has only been tested against a simulated API, not a real Zendesk account. Without credentials it runs in dry-run mode.

## Why human-in-the-loop

The companion project, [agent-triage-kit](https://github.com/monicaslgc/agent-triage-kit), explores the opposite end: agents that resolve tickets on their own once confidence clears a threshold. This project explores the other side of the same question: when the AI should only suggest, and how you measure whether it has earned more autonomy.

Every review stores what the AI proposed next to what the human decided. `/metrics` turns that into the numbers that justify automating more (for example, auto-approving P4 tickets once agreement is consistently high).

```
ticket -> classify (LLM or rules) -> RAG over KB -> draft reply -> review queue -> human decides -> metrics
```

## Run it

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload     # interactive docs at http://127.0.0.1:8000/docs
pytest
```

Without `ANTHROPIC_API_KEY` it classifies with keyword rules (offline, deterministic). With the key it uses Claude (`TRIAGE_MODEL`, default `claude-haiku-5-5`) and falls back to the rules if the API call fails. The LLM path is not covered by the offline test suite.

## API

| Method | Route | Purpose |
|---|---|---|
| POST | `/tickets` | Triage a ticket and draft a reply (stays `pending_review`) |
| GET | `/tickets?status=` | Review queue, most urgent first |
| POST | `/tickets/{id}/review` | Human approves or rejects; can correct priority, category and reply |
| POST | `/webhooks/zendesk` | Zendesk-style inbound webhook |
| GET | `/outbox` | Audit log of calls to Zendesk (`sent`, `dry_run`, `error`) |
| GET | `/metrics` | Human vs AI agreement and reply edit rate |

## Zendesk-style flow (concept)

```
ticket created -> webhook -> /webhooks/zendesk
  -> AI triage -> INTERNAL note + priority + type + tags on the ticket
  -> agent reviews -> approved: PUBLIC reply, status "pending"
                      rejected: internal note + tag ai_rejected
```

- P1/P2/P3/P4 map to Zendesk `urgent`/`high`/`normal`/`low`; bug maps to `incident`, feature request to `task`, everything else to `question`.
- The customer never sees the AI suggestion, only the reply a human approved.
- Tags are added with `additional_tags`; `tags` would overwrite the ones an agent already set.
- The webhook is idempotent per ticket, since Zendesk retries failed deliveries.
- Webhook signatures (HMAC-SHA256) are verified when `ZENDESK_WEBHOOK_SECRET` is set.
- If Zendesk is unreachable, the triage and the human's decision are kept and the failure is logged in `/outbox`. There is no automatic retry yet.

Environment variables: `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN` (enable real calls), `ZENDESK_WEBHOOK_SECRET`, `ANTHROPIC_API_KEY`, `TRIAGE_DB`.

## Tests

27 tests, including end-to-end flows against a stateful fake Zendesk that applies the real update semantics (additive tags, public vs internal comments). I checked that they catch real bugs by breaking the code on purpose (overwriting tags, making the triage note public, removing the webhook idempotency check): each break made tests fail.

## Known limitations

- Retrieval is TF-IDF over four sample articles, so it returns occasional irrelevant matches. Embeddings (for example pgvector) are the next step.
- Rule-based classification is a baseline, not a production classifier.
- No automatic retry for failed Zendesk calls; no check on the webhook timestamp age.
- No authentication on the review endpoints.

## Next steps

- Embeddings with pgvector and PostgreSQL instead of TF-IDF and SQLite
- Retry of failed `/outbox` entries
- A small review-queue UI
- A labelled evaluation set to compare rules against the LLM
