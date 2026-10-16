# Ticket system integrations

How tickets get in, what is written back, and how to connect Zammad or Zendesk. Back to the [README](../README.md).

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

Every environment variable is listed in [API and configuration](api.md#configuration).

Always set the webhook secret in production; without it the webhook endpoint accepts any caller.

## Connecting Zammad
Zammad is open source and self-hostable (see [zammad-docker-compose](https://github.com/zammad/zammad-docker-compose)).
1. Create an API token (Profile > Token Access) with `ticket.agent` permission and set `ZAMMAD_URL` and `ZAMMAD_TOKEN`.
2. Admin > Webhook: endpoint `https://<your-host>/webhooks/zammad`, optionally an HMAC SHA1 signature token matching `ZAMMAD_WEBHOOK_SECRET`. The adapter reads Zammad's default payload (`{"ticket": {...}, "article": {...}}`).
3. Admin > Trigger: "ticket created" -> notify webhook. Use *created*, not *updated*, so the adapter's own notes don't loop back.

The Zammad payload and API calls are based on its documentation and should be verified against your instance and version before relying on them.

## Connecting Zendesk
1. Webhook body: `{"ticket_id":"{{ticket.id}}","subject":"{{ticket.title}}","description":"{{ticket.description}}","requester":"{{ticket.requester.name}}"}`, endpoint `https://<your-host>/webhooks/zendesk`, signing secret as `ZENDESK_WEBHOOK_SECRET`.
2. Trigger on "ticket created" -> notify the webhook. Create an API token under Apps and integrations > APIs.

## Adding another system
Implement `TicketSystem` (`support_copilot/ticketsystems/base.py`): `verify_webhook`, `parse_webhook`, `post_internal_note`, `post_public_reply`, `post_rejection`, plus `base_url`/auth. Register it in `build_systems()`, or pass it to your own `Copilot(systems={...})`. Add a stateful fake to `tests/fakes.py` with the same read API (`public`, `internal`, `tags`, `priority`) and add it to the `env` fixture params: the whole end-to-end suite then runs against it.
