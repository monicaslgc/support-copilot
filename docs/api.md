# API and configuration

Back to the [README](../README.md).

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | The review queue page (no data of its own: it calls the API below) |
| POST | `/tickets` | Triage a ticket and draft a reply (stays `pending_review`) |
| GET | `/tickets?status=` | Review queue, most urgent first. Reviewed tickets include a `review` with the human's decision and the reply that was sent |
| GET | `/clusters` | Groups of tickets about the same issue that still have something to review, possible incidents first |
| POST | `/clusters/{id}/review` | One decision for every pending ticket of a possible incident (409 for other groups) |
| POST | `/check-reply` | Promises (refunds, guarantees, amounts, deadlines) in a reply about to be sent |
| POST | `/tickets/{id}/review` | Human approves or rejects; can correct priority, category and reply |
| POST | `/webhooks/{system}` | Inbound webhook from a ticket system (`zammad`, `zendesk`) |
| GET | `/outbox` | Audit log of calls to ticket systems (`sent`, `dry_run`, `error`) |
| GET | `/metrics` | Human vs AI agreement and reply edit rate |
| GET | `/metrics/breakdown` | Per category: clean rate, its lower bound, rejections, reasons, automation candidates |
| GET | `/health` | Liveness, whether the LLM is enabled, which ticket systems are configured |

Set `API_KEY` and send it as `X-API-Key` to protect everything except `/health` and the webhooks (which use each system's own signature). If `API_KEY` is not set the API is open, which is only fine on a local machine.

## Configuration

All settings are environment variables. Nothing is required: without any of them the app runs offline with rules and a local SQLite file.

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` | not set | Turns on Claude for triage and drafts. Without it the rules and the template are used |
| `TRIAGE_MODEL` | `claude-opus-5-5` | Model used for triage and drafts, for example `claude-haiku-5-5` for cheaper bulk triage |
| `TRIAGE_DB` | `triage.db` | Path of the SQLite file |
| `API_KEY` | not set | When set, every route except `/health`, `/` and the webhooks needs `X-API-Key` |
| `ZAMMAD_URL`, `ZAMMAD_TOKEN` | not set | Connect the Zammad adapter (otherwise it runs in dry-run) |
| `ZAMMAD_WEBHOOK_SECRET` | not set | Verifies the HMAC-SHA1 signature of Zammad webhooks |
| `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN` | not set | Connect the Zendesk adapter (otherwise it runs in dry-run) |
| `ZENDESK_WEBHOOK_SECRET` | not set | Verifies the HMAC-SHA256 signature of Zendesk webhooks |
| `CLUSTER_WINDOW_MIN` | `60` | Minutes within which tickets can be linked as the same issue or a duplicate |
| `SPIKE_MIN_CUSTOMERS` | `3` | Different customers needed for a possible incident |
| `AUTOMATION_MIN_REVIEWS` | `20` | Reviews a category needs before it can be an automation candidate |
| `AUTOMATION_MIN_LOWER_BOUND` | `0.9` | Lower bound of the clean rate a category needs to be a candidate |

Set the webhook secrets and `API_KEY` before connecting a real ticket system. At startup the app logs a warning if a system is connected and one of them is missing.
