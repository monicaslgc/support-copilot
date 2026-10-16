# Ticket system integrations

How tickets get in, what is written back, and how to connect Zammad or Zendesk. Back to the [README](../README.md).

```
ticket created -> webhook -> /webhooks/{system}
  -> AI triage -> INTERNAL note + priority + tags on the ticket
  -> agent tags the ticket (ai_approve / ai_reject) -> "ticket updated" webhook -> /webhooks/{system}
       approved: PUBLIC reply     rejected: internal note + tag ai_rejected
  (or the agent decides in the optional review page or through the API: the same result)
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

## Deciding from inside the helpdesk

The internal note on every ticket carries the instructions: add the tag `ai_approve` to send the suggested reply, `ai_reject` to discard it and answer yourself. Your helpdesk then calls the webhook again with the ticket's tags, and the add-on acts:

- **`ai_approve`**: the drafted reply goes to the customer as a public reply, once. The decision is recorded with the agent who tagged the ticket (the `agent` / `updated_by` of the payload) as the reviewer.
- **`ai_reject`**: nothing goes to the customer; an internal note and the tag `ai_rejected` are added.
- **Both tags**, or any update without them: nothing happens. A repeated delivery, or the update caused by our own reply, is recognised and ignored.
- A ticket that arrives already carrying a decision tag is triaged as new: there is no draft yet to approve.
- Replies are sent exactly as drafted. To change the text, answer in the helpdesk yourself (reject), or use the review page.

A decision sends a reply to a customer, so for a connected system it is **only accepted when that system's webhook secret is set** (`ZENDESK_WEBHOOK_SECRET` / `ZAMMAD_WEBHOOK_SECRET`): the signature is what proves the tag came from your helpdesk. Without the secret the update is ignored and a warning is logged. `Copilot(allow_unsigned_decisions=True)` accepts it anyway, for a trusted network only; a system without credentials (dry-run) sends nothing, so it never needs the secret.

Who may do this is whoever can edit the ticket in your helpdesk, since that is who can add a tag. To narrow it, pass `Copilot(allowed_reviewers={"ana@acme.com", ...})`; anyone else's tag is ignored and logged. To switch the feature off, use `Taxonomy(helpdesk_decisions=False)`. The tag names, and every other tag and label written, can be changed ([how](embedding.md#your-tags-names-and-who-may-decide)).

Setup, besides the "ticket created" webhook below: a second trigger for **tags changed** (when the ticket has `ai_approve` or `ai_reject`) that calls the same endpoint, with a payload that includes the ticket's tags and the person who made the change. Zendesk: add `"tags":"{{ticket.tags}}","agent":"{{current_user.email}}"` to the webhook body. Zammad: the adapter reads `ticket.tags` and `ticket.updated_by` (an object with `email` or `login`). Zammad's documentation shows `updated_by` in the default ticket payload; whether the default payload carries the ticket's tags is not confirmed, so check your version's payload, or use the custom payload option, before relying on it. These payload shapes are checked against fakes only.

## Connecting Zammad
Zammad is open source and self-hostable (see [zammad-docker-compose](https://github.com/zammad/zammad-docker-compose)).
1. Create an API token (Profile > Token Access) with `ticket.agent` permission and set `ZAMMAD_URL` and `ZAMMAD_TOKEN`.
2. Admin > Webhook: endpoint `https://<your-host>/webhooks/zammad`, optionally an HMAC SHA1 signature token matching `ZAMMAD_WEBHOOK_SECRET`. The adapter reads Zammad's default payload (`{"ticket": {...}, "article": {...}}`).
3. Admin > Trigger: "ticket created" -> notify webhook. Use *created* for new tickets; for decisions add the tag trigger described above. Updates that carry no decision tag (including those caused by the adapter's own notes) are ignored.

The Zammad payload and API calls are based on its documentation and should be verified against your instance and version before relying on them. In particular, an approved reply is posted as a non-internal `note`; whether your customer is also emailed depends on the article type (see [limitations](limitations.md)).

## Connecting Zendesk
1. Webhook body: `{"ticket_id":"{{ticket.id}}","subject":"{{ticket.title}}","description":"{{ticket.description}}","requester":"{{ticket.requester.name}}"}`, endpoint `https://<your-host>/webhooks/zendesk`, signing secret as `ZENDESK_WEBHOOK_SECRET`.
2. Trigger on "ticket created" -> notify the webhook (and the tag trigger described above for decisions). Create an API token under Apps and integrations > APIs.

## Adding another system
Implement `TicketSystem` (`support_copilot/ticketsystems/base.py`): `verify_webhook`, `parse_webhook`, `post_internal_note`, `post_public_reply`, `post_rejection`, plus `base_url`/auth. Register it in `build_systems()`, or pass it to your own `Copilot(systems={...})`. Add a stateful fake to `tests/fakes.py` with the same read API (`public`, `internal`, `tags`, `priority`) and add it to the `env` fixture params: the whole end-to-end suite then runs against it.
