# support-copilot
[![tests](https://github.com/monicaslgc/support-copilot/actions/workflows/tests.yml/badge.svg)](https://github.com/monicaslgc/support-copilot/actions/workflows/tests.yml)

A human-in-the-loop AI copilot for support tickets. It triages incoming tickets (category, P1-P4 priority, owning team), finds relevant knowledge-base articles (RAG) and drafts a reply. **Nothing reaches the customer until a human approves it.**

It plugs into ticket systems through a small adapter interface. Two adapters ship today: **Zammad** and **Zendesk**.

> **Status: concept / portfolio project**, built from scratch with synthetic data. Both adapters follow the vendors' documented API and webhook shapes, but have only been tested against stateful fakes of those APIs, **not against a live Zendesk or Zammad instance**. Without credentials they run in dry-run mode.

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
| POST | `/webhooks/{system}` | Inbound webhook from a ticket system (`zammad`, `zendesk`) |
| GET | `/outbox` | Audit log of calls to ticket systems (`sent`, `dry_run`, `error`) |
| GET | `/metrics` | Human vs AI agreement and reply edit rate |

## Ticket system integrations

```
ticket created -> webhook -> /webhooks/{system}
  -> AI triage -> INTERNAL note + priority + tags on the ticket
  -> agent reviews -> approved: PUBLIC reply
                      rejected: internal note + tag ai_rejected
```

Guarantees, enforced by the same end-to-end tests for every system:
- The customer never sees the AI suggestion, only the reply a human approved.
- Tags are added, never replaced, so the ones an agent already set survive.
- Webhooks are idempotent per `(system, ticket id)`, since ticket systems retry failed deliveries. Two systems can use the same ticket id without colliding.
- If the ticket system is unreachable, the triage and the human's decision are kept and the failure is logged in `/outbox`. There is no automatic retry yet.
- Dry-run records the exact requests that would be sent.

| | Zammad | Zendesk |
|---|---|---|
| Auth | `Authorization: Token token=...` | Basic `email/token:api_token` |
| Webhook signature | `X-Hub-Signature: sha1=<HMAC-SHA1>` | `X-Zendesk-Webhook-Signature` (HMAC-SHA256 of timestamp + body) |
| Priority | P1, P2 -> high; P3 -> normal; P4 -> low (no "urgent" by default; P1 vs P2 stays in the tags) | urgent / high / normal / low |
| Internal vs public | `ticket_articles` with `internal: true/false` | comment `public: false/true` |
| Tags | `POST /tags/add` (additive) | `additional_tags` (additive; `tags` would overwrite) |

Environment variables:
- Zammad: `ZAMMAD_URL`, `ZAMMAD_TOKEN`, `ZAMMAD_WEBHOOK_SECRET`
- Zendesk: `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN`, `ZENDESK_WEBHOOK_SECRET`
- Core: `ANTHROPIC_API_KEY`, `TRIAGE_MODEL`, `TRIAGE_DB`

Always set the webhook secret in production; without it the webhook endpoint accepts any caller.

### Connecting Zammad
Zammad is open source and self-hostable (see [zammad-docker-compose](https://github.com/zammad/zammad-docker-compose)).
1. Create an API token (Profile > Token Access) with `ticket.agent` permission and set `ZAMMAD_URL` and `ZAMMAD_TOKEN`.
2. Admin > Webhook: endpoint `https://<your-host>/webhooks/zammad`, optionally an HMAC SHA1 signature token matching `ZAMMAD_WEBHOOK_SECRET`. The adapter reads Zammad's default payload (`{"ticket": {...}, "article": {...}}`).
3. Admin > Trigger: "ticket created" -> notify webhook. Use *created*, not *updated*, so the adapter's own notes don't loop back.

The Zammad payload and API calls are based on its documentation and should be verified against your instance and version before relying on them.

### Connecting Zendesk
1. Webhook body: `{"ticket_id":"{{ticket.id}}","subject":"{{ticket.title}}","description":"{{ticket.description}}","requester":"{{ticket.requester.name}}"}`, endpoint `https://<your-host>/webhooks/zendesk`, signing secret as `ZENDESK_WEBHOOK_SECRET`.
2. Trigger on "ticket created" -> notify the webhook. Create an API token under Apps and integrations > APIs.

### Adding another system
Implement `TicketSystem` (`app/ticketsystems/base.py`): `verify_webhook`, `parse_webhook`, `post_internal_note`, `post_public_reply`, `post_rejection`, plus `base_url`/auth. Register it in `build_systems()`. Add a stateful fake to `tests/fakes.py` with the same read API (`public`, `internal`, `tags`, `priority`) and add it to the `env` fixture params: the whole end-to-end suite then runs against it.

## Tests

40 tests. The end-to-end scenarios run against stateful fakes of both APIs that apply each system's update semantics (additive tags, public vs internal), and assert the final state of the ticket, not just the requests sent. I checked that they catch real bugs by breaking the code on purpose (public/internal swapped, tags overwritten, wrong priority map, wrong signature algorithm, missing idempotency, ids colliding across systems): each break made tests fail.

## Known limitations

- Not verified against live Zammad or Zendesk instances (see status above).
- Retrieval is TF-IDF over four sample articles, so it returns occasional irrelevant matches. Embeddings (for example pgvector) are the next step.
- Rule-based classification is a baseline, not a production classifier.
- No automatic retry for failed ticket-system calls; no check on webhook timestamp age.
- No authentication on the review endpoints. A multi-request update (for example Zammad's priority + note + tags) is not transactional: if one call fails midway, the earlier ones stay applied.

## Next steps

- Run the Zammad adapter against a real self-hosted instance and fix whatever differs from the docs
- Embeddings with pgvector and PostgreSQL instead of TF-IDF and SQLite
- Retry of failed `/outbox` entries
- A small review-queue UI
- A labelled evaluation set to compare rules against the LLM
