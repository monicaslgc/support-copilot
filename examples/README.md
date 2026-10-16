# Examples

Payloads and commands you can run against a local server. Start it first (see the [README](../README.md#try-it-with-the-reference-server)):

```bash
uvicorn support_copilot.main:app --reload
```

Nothing below needs a ticket-system account: without credentials the adapters run in **dry-run**, which records the exact requests they would send under `/outbox` and sends nothing.

## A ticket arrives from Zammad

[`zammad_webhook.json`](zammad_webhook.json) is the shape of Zammad's default webhook payload.

```bash
curl -s localhost:8000/webhooks/zammad -H 'content-type: application/json' \
     --data-binary @examples/zammad_webhook.json | python -m json.tool
```

The answer has the triage and the draft reply. The ticket now waits for a person in the review page at <http://127.0.0.1:8000/>.

## A ticket arrives from Zendesk

[`zendesk_webhook.json`](zendesk_webhook.json) is the body template from [the integration guide](../docs/integrations.md#connecting-zendesk).

```bash
curl -s localhost:8000/webhooks/zendesk -H 'content-type: application/json' \
     --data-binary @examples/zendesk_webhook.json | python -m json.tool
```

## What the ticket system would receive

```bash
curl -s localhost:8000/outbox | python -m json.tool
```

After the commands above you see one `triage_note` per ticket: an **internal** note with the priority and tags. The customer sees nothing yet.

## Approve a reply

```bash
curl -s localhost:8000/tickets/1/review -H 'content-type: application/json' \
     -d '{"action": "approved", "reviewer": "ana", "edited_reply": "Hi Rui, we are looking into the export problem."}'
```

`/outbox` now also has a `public_reply`: that is the first and only thing the customer would see.

## Signed webhooks

With a secret set, a webhook without a valid signature is refused. Start the server with `ZAMMAD_WEBHOOK_SECRET=s3cret` and `ZENDESK_WEBHOOK_SECRET=s3cret`, then:

```bash
# Zammad: X-Hub-Signature is sha1= followed by the HMAC-SHA1 of the body
SIG=$(openssl dgst -sha1 -hmac s3cret < examples/zammad_webhook.json | awk '{print $NF}')
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/webhooks/zammad -H 'content-type: application/json' \
     -H "X-Hub-Signature: sha1=$SIG" --data-binary @examples/zammad_webhook.json      # 201

curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/webhooks/zammad -H 'content-type: application/json' \
     --data-binary @examples/zammad_webhook.json                                       # 401

# Zendesk: base64 of the HMAC-SHA256 of the timestamp followed by the body
TS=2026-10-16T10:00:00Z
SIG=$( (printf '%s' "$TS"; cat examples/zendesk_webhook.json) | openssl dgst -sha256 -hmac s3cret -binary | base64)
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/webhooks/zendesk -H 'content-type: application/json' \
     -H "X-Zendesk-Webhook-Signature-Timestamp: $TS" -H "X-Zendesk-Webhook-Signature: $SIG" \
     --data-binary @examples/zendesk_webhook.json                                      # 201
```

## Walk through the user stories

[`scripts/demo.py`](../scripts/demo.py) plays each scenario from the [user stories](../docs/user-stories.md) against a running server and checks the outcome: `python scripts/demo.py all`.
