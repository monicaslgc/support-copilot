# API and configuration

Back to the [README](../README.md).

## API

These are the routes of `create_router(copilot)` (see the [embedding guide](embedding.md)); the reference server mounts them at the root and adds `/` and `/health`. Each has a `Copilot` method of the same purpose, so you can use it without HTTP.

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | The review queue page (no data of its own: it calls the API below) |
| POST | `/tickets` | Triage a ticket and draft a reply (stays `pending_review`) |
| GET | `/tickets?status=` | Review queue, most urgent first. Reviewed tickets include a `review` with the human's decision and the reply that was sent |
| GET | `/clusters` | Groups of tickets about the same issue that still have something to review, possible incidents first |
| POST | `/clusters/{id}/review` | One decision for every pending ticket of a possible incident (409 for other groups) |
| POST | `/check-reply` | Promises (refunds, guarantees, amounts, deadlines) in a reply about to be sent |
| POST | `/tickets/{id}/review` | Human approves or rejects; can correct priority, category and reply |
| POST | `/webhooks/{system}` | Inbound webhook from a ticket system (`zammad`, `zendesk`): a new ticket is triaged and queued; an update that adds `ai_approve` or `ai_reject` to a ticket already queued is the person's decision (see below) |
| GET | `/outbox` | Audit log of calls to ticket systems (`sent`, `dry_run`, `error`) |
| GET | `/metrics` | Human vs AI agreement and reply edit rate |
| GET | `/metrics/breakdown` | Per category: clean rate, its lower bound, rejections, reasons, automation candidates |
| GET | `/health` | Liveness, whether the LLM is enabled, which ticket systems are configured |

Set `API_KEY` and send it as `X-API-Key` to protect everything except `/health` and the webhooks (which use each system's own signature). If `API_KEY` is not set the API is open, which is only fine on a local machine.

## What the webhook does

- **Signature first.** With the system's webhook secret set, a request without a valid signature gets `401`. A body over 1 MB gets `413` without being read.
- **A new ticket** is triaged and queued (`201`, with the triage and the draft). A re-sent webhook is recognised and changes nothing. A long ticket is cut to its first 10,000 characters and a short or empty field is filled in, so a helpdesk is never told to retry something that can be handled (`422` only for a payload that is not a ticket at all).
- **An update to a ticket already queued** is read for the decision tags. `ai_approve` sends the drafted reply once and `ai_reject` sends nothing; both are recorded under the agent named in the payload. Both tags at once, any other update, a repeat, and a decision from someone not in `allowed_reviewers` change nothing, and the response says why (`ignored`). For a connected system a decision also needs the webhook secret to be set. [Details](integrations.md#deciding-from-inside-the-helpdesk)

## Configuration

All settings of the reference server are environment variables. (When you embed the library, the same settings are arguments of `Copilot`: see the [embedding guide](embedding.md).) Check them with `python -m support_copilot.doctor`. Nothing is required: without any of them the app runs offline with rules and a local SQLite file.

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` | not set | Turns on Claude for triage and drafts. Without it the rules and the template are used |
| `TRIAGE_MODEL` | `claude-opus-5-5` | Model used for triage and drafts, for example `claude-haiku-5-5` for cheaper bulk triage |
| `TRIAGE_DB` | `triage.db` | Path of the SQLite file |
| `KB_DIR` | the bundled samples | Folder of markdown help articles (the first line of each is its title; an optional `Keywords:` line lists words customers use) |
| `API_KEY` | not set | When set, every route except `/health`, `/` and the webhooks needs `X-API-Key` |
| `ZAMMAD_URL`, `ZAMMAD_TOKEN` | not set | Connect the Zammad adapter (otherwise it runs in dry-run) |
| `ZAMMAD_WEBHOOK_SECRET` | not set | Verifies the HMAC-SHA1 signature of Zammad webhooks |
| `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN` | not set | Connect the Zendesk adapter (otherwise it runs in dry-run) |
| `ZENDESK_WEBHOOK_SECRET` | not set | Verifies the HMAC-SHA256 signature of Zendesk webhooks |
| `TAG_PREFIX` | `ai_` | In front of every tag written to the helpdesk (`ai_triaged`, `ai_cat_...`) |
| `APPROVE_TAG`, `REJECT_TAG` | `approve`, `reject` | The tags (after the prefix) an agent adds to send or discard the draft |
| `HELPDESK_DECISIONS` | `1` | `0` turns off approving by tag: only the review page and the API decide |
| `ALLOWED_REVIEWERS` | everyone who can edit the ticket | Comma separated emails who may decide by tag; anyone else's tag is ignored |
| `ALLOW_UNSIGNED_DECISIONS` | `0` | `1` accepts a decision by tag for a connected system without its webhook secret: a trusted network only |
| `CATEGORY_LABELS`, `TEAM_LABELS` | not set | Your names for ours, like `billing=finance-queries,bug=defects`; they appear in tags and notes |
| `CLUSTER_WINDOW_MIN` | `60` | Minutes within which tickets can be linked as the same issue or a duplicate |
| `SPIKE_MIN_CUSTOMERS` | `3` | Different customers needed for a possible incident |
| `AUTOMATION_MIN_REVIEWS` | `20` | Reviews a category needs before it can be an automation candidate |
| `AUTOMATION_MIN_LOWER_BOUND` | `0.9` | Lower bound of the clean rate a category needs to be a candidate |

Set the webhook secrets and `API_KEY` before connecting a real ticket system. At startup the app logs a warning if a system is connected and one of them is missing.
