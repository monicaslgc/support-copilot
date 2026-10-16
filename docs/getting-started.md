# Run it against your own environment

A step-by-step path from "I have read the README" to "agents in my helpdesk approve AI drafts by tagging tickets", with a check at every step and a way back. Back to the [README](../README.md).

The order matters: each stage can go wrong without touching a customer, and the next stage only starts when the one before looks right.

| Stage | What you do | What can reach a customer |
|---|---|---|
| 1. Offline | Run the triage over an export of your own tickets | Nothing. No helpdesk is called |
| 2. Dry-run | The server receives your helpdesk's webhooks but has no credentials to write back | Nothing. It records what it *would* write |
| 3. Shadow | Add credentials: internal notes and tags appear on tickets. Approval by tag is off | Nothing. Notes are internal |
| 4. Pilot | Turn on approval by tag for a few named agents, in one group or a sandbox | A reply, only after a named agent tags the ticket |
| 5. Wider | Add agents and groups | The same |

This is a concept project: the adapters and the tag payloads have only been tested against fakes of Zendesk and Zammad (see [what is verified](../README.md#what-is-verified)). Use a sandbox or a test group for stages 2 to 4, and treat the first real run as the first real test.

## What you need

- Python 3.11 or newer, on a machine or container your helpdesk can reach over **HTTPS** (webhooks are calls from the helpdesk to you).
- Admin rights in your helpdesk to create a webhook, triggers and an API token.
- Your help articles as markdown files (below). Optional, but they are what drafts are grounded on.
- Optional: an `ANTHROPIC_API_KEY` for model-backed triage and drafts. Without it the rules and templates run, which is fine to start with.

**What leaves your network.** With no API key, only the calls to your helpdesk's API. With a key, also the ticket text to Anthropic, with emails, phone numbers, card numbers, IBANs and passwords masked first (names and addresses are not masked: see [guardrails](features.md#guardrails-around-the-model)). The ticket text and every decision are stored, unmasked, in a SQLite file you control.

## 1. Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install "support-copilot[server] @ git+https://github.com/monicaslgc/support-copilot"
```

`[server]` adds FastAPI, uvicorn and the Anthropic SDK. Embedding it in your own app instead? See the [embedding guide](embedding.md); the checks below work for you too.

## 2. Your help articles

Put one markdown file per article in a folder. The first line is the title, and an optional `Keywords:` line lists the words customers use that the article itself does not:

```markdown
# Reset your password
Keywords: forgot, locked out, login, 2fa
Go to Settings > Security and choose "Reset password"...
```

Start with the 10 to 30 articles your agents link most. A ticket that matches no article gets a draft that only says a specialist will reply, so a missing article shows up as a plain reply, not a wrong one. Point the server at the folder with `KB_DIR`.

## 3. Try it on your own tickets, offline

Export some past tickets (a CSV with a header, a JSON list or JSON lines). Each needs `subject` and `body`. If you add `category` and `priority` columns with what your agents decided (our names: `bug`, `billing`, `access`, `how_to`, `feature_request`, `other`, and `P1` to `P4`), you also get an agreement rate.

```bash
python -m support_copilot.evaluate tickets.csv --kb our-articles/ --rules --out results.csv
```

(`--rules` never calls Claude; leave it out to use Claude, at up to two model calls per ticket: one to triage it and, when a help article matches, one to draft the reply.)

What to look at:

- **A help article was found for X%.** Low means missing articles or missing `Keywords:`. Read the tickets in `results.csv` with an empty `article`.
- **By priority.** If almost everything is P1, or nothing is, the rules do not fit your tickets: try Claude, and compare.
- **Agreement with your agents,** and the list of "your agents said X, the triage said Y". The rule-based triage is a baseline, not a classifier: expect to see where it is wrong, and use that to decide whether to turn on Claude.
- **The first drafts,** read as a customer would. `--preview 10` shows more.

No helpdesk is involved and nothing is stored.

## 4. Configure

The reference server reads environment variables only (there is no config file or `.env` loader: use your shell, a systemd `EnvironmentFile=`, or `docker run --env-file`). Nothing is required. The ones you will set first:

| Variable | Set it to | Why |
|---|---|---|
| `TRIAGE_DB` | a path on persistent disk, e.g. `/var/lib/support-copilot/triage.db` | the queue and every decision live here; default `./triage.db` |
| `KB_DIR` | your articles folder | otherwise only the six sample articles are used |
| `API_KEY` | a long random string | protects the review API (`/tickets`, `/outbox`, `/metrics` and the rest): every request then needs `X-API-Key`. The page shell at `/`, `/health`, `/docs`, `/openapi.json` and the webhooks do not need it (the webhooks use their own signature) |
| `ZENDESK_WEBHOOK_SECRET` / `ZAMMAD_WEBHOOK_SECRET` | the signing secret your helpdesk shows | proves a webhook came from your helpdesk; **approval by tag is refused without it** |
| `ANTHROPIC_API_KEY` | your key | turns on Claude (model: `TRIAGE_MODEL`) |

Your own tag names, category and team names, and who may approve are set the same way: `TAG_PREFIX`, `APPROVE_TAG`, `REJECT_TAG`, `CATEGORY_LABELS`, `TEAM_LABELS`, `ALLOWED_REVIEWERS`, `HELPDESK_DECISIONS`. Every variable, with its default, is in [API and configuration](api.md#configuration). A setting that cannot work (`CATEGORY_LABELS=billing`, a tag with a space) stops the start with a message that names it.

```bash
export TRIAGE_DB=/var/lib/support-copilot/triage.db KB_DIR=/srv/articles API_KEY="$(openssl rand -hex 24)"
export ZENDESK_WEBHOOK_SECRET=...        # the same value you give the helpdesk in step 7
export ALLOWED_REVIEWERS=ana@acme.com,rui@acme.com
```

## 5. Check the configuration

```bash
python -m support_copilot.doctor            # reads the same variables as the server; nothing leaves this machine
python -m support_copilot.doctor --live     # also asks each connected helpdesk "who am I?" (read-only)
python -m support_copilot.doctor --model    # also makes one small call to Claude
```

Each line is `ok`, `warn` (it works, but look) or `FAIL` (it will not work), and the exit code is 1 if anything fails. It checks the database, your articles, the model, the API key, each helpdesk's credentials and webhook secret, and the tags that will be written (the main ones, and which others appear when they apply). `--live` creates, changes and sends nothing. Fix every `FAIL` before going on.

## 6. Run the server

```bash
uvicorn support_copilot.main:app --host 127.0.0.1 --port 8000
```

- **One process.** The design is one instance with one SQLite file. Do not run several workers or several copies on the same file.
- **HTTPS in front.** The server speaks plain HTTP. Put a reverse proxy or load balancer with TLS in front and give your helpdesk the HTTPS address.
- **Expose only the webhooks.** The helpdesk needs `POST /webhooks/zendesk` and `/webhooks/zammad`. The review page (`/`) and the API return customer messages: keep them on your internal network or VPN, and set `API_KEY` regardless. With nginx, for example, proxy `location /webhooks/` and leave everything else unproxied from the internet.
- `GET /health` says whether Claude is on and which helpdesks are connected.

A minimal systemd unit (an example: adjust the paths and user, and check it on your system):

```ini
[Service]
User=support-copilot
EnvironmentFile=/etc/support-copilot.env
ExecStart=/opt/support-copilot/.venv/bin/uvicorn support_copilot.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
```

## 7. Connect your helpdesk

Start at **stage 2: dry-run**. Set the webhook secret but leave the helpdesk API credentials out, so nothing is written back yet. The menus below are the usual ones; names change between versions.

### Zendesk

1. **Webhook.** Admin Center > Apps and integrations > Webhooks > Create webhook. Endpoint `https://your-host/webhooks/zendesk`, method `POST`, format `JSON`. Copy the signing secret into `ZENDESK_WEBHOOK_SECRET` and restart.
2. **Trigger for new tickets.** Admin Center > Objects and rules > Business rules > Triggers. Condition: ticket is created. Action: notify active webhook, with this JSON body:

   ```json
   {"ticket_id":"{{ticket.id}}","subject":"{{ticket.title}}","description":"{{ticket.description}}","requester":"{{ticket.requester.name}}"}
   ```
3. **API token** (stage 3). Admin Center > Apps and integrations > APIs > Zendesk API > add a token. Set `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL` (the agent the token belongs to: it must be an agent or admin) and `ZENDESK_API_TOKEN`. Run `doctor --live`.
4. **Trigger for decisions** (stage 4). Condition: tags contain `ai_approve`, or tags contain `ai_reject` (use your own names if you changed them). Action: notify the same webhook, with the same body plus the tags and the person:

   ```json
   {"ticket_id":"{{ticket.id}}","subject":"{{ticket.title}}","description":"{{ticket.description}}","requester":"{{ticket.requester.name}}","tags":"{{ticket.tags}}","agent":"{{current_user.email}}"}
   ```

### Zammad

1. **Webhook.** Settings > System > Webhook > new. Endpoint `https://your-host/webhooks/zammad`, and a signature token that you also set as `ZAMMAD_WEBHOOK_SECRET`.
2. **Trigger for new tickets.** Settings > Manage > Trigger. Execute on: ticket creation. Action: webhook.
3. **API token** (stage 3). Profile > Token Access, with the `ticket.agent` permission. Set `ZAMMAD_URL` and `ZAMMAD_TOKEN`. Run `doctor --live`.
4. **Trigger for decisions** (stage 4). Execute on ticket update, with the ticket's tags containing `ai_approve` or `ai_reject`, and action: webhook. The adapter reads `ticket.tags` and `ticket.updated_by` from the payload. Zammad documents `updated_by` in its default payload; whether the default payload also carries the tags is **not confirmed**, so check one real delivery (the webhook's log in Zammad) and use the custom payload option if the tags are missing.

Details, the guarantees, and the payload shapes are in [Ticket system integrations](integrations.md).

## 8. Verify end to end

Do this with a test ticket in a sandbox or a test group, once per stage.

**Stage 2, dry-run.** Create a ticket. Within a few seconds:

```bash
curl -s -H "X-API-Key: $API_KEY" localhost:8000/outbox | python -m json.tool | head -30
```

The newest entry has `"kind": "triage_note"` and `"mode": "dry_run"`: the request it would have sent. The ticket is in the review page. Nothing changed in the helpdesk.

**Stage 3, shadow.** Add the credentials and restart, then create another ticket. In the helpdesk you now see an **internal** note ("[AI triage - pending human review]") with the category, priority, team, the help article, the suggested reply and how to decide; the tags `ai_triaged`, `ai_cat_...`, `ai_team_...`; and the priority set (on Zendesk also the ticket type, taken from the category). The outbox entry says `"mode": "sent"`. The customer sees nothing. If the entry says `"mode": "error"`, its `error` field says why (see the table below).

**Stage 4, pilot.** Add the decision trigger and `ALLOWED_REVIEWERS`. As one of those agents, add `ai_approve` to the test ticket. The customer-facing reply appears as a public reply, `/outbox` has a `public_reply` (on Zendesk the ticket status becomes `pending`), and the review page shows the ticket approved by that agent's email. Add the tag again, or save the ticket again: nothing more is sent. On another test ticket, add `ai_reject`: no reply, an internal note and the tag `ai_rejected`.

## Turning it off

In order of how much you want to stop:

1. Disable the decision trigger (or set `HELPDESK_DECISIONS=0`): tags no longer send anything. Approval is only in the review page.
2. Disable the creation trigger: new tickets stop arriving. Tickets already queued stay in the review page.
3. Unset the helpdesk credentials and restart: it goes back to dry-run and writes nothing.

Nothing else in your helpdesk depends on it. The tags it added stay on the tickets until you remove them.

## Running it

- **Logs.** Warnings (a model fallback, a failed helpdesk call, a decision that was ignored and why) go to the `support_copilot` logger and appear on stderr.
- **Failures.** A failed call to your helpdesk never loses the triage or a decision: it is recorded in `GET /outbox` with `"mode": "error"`. There is no automatic retry yet.
- **How the AI is doing.** `GET /metrics/breakdown`, or the Insights tab of the review page: per category, how often agents approve untouched, with a confidence bound, and why they correct it. Nothing is automated from it.
- **Backups.** Back up the SQLite file (`sqlite3 triage.db ".backup backup.db"` is safe while the server runs). It holds ticket text and decisions: protect it like the helpdesk itself. There is no retention job yet; if your policy needs one, that is a manual step on this file.
- **Upgrades.** The database is upgraded on start. Back it up first, then `pip install --upgrade` and restart.
- Before connecting real customer data, read [SECURITY](../SECURITY.md).

## When something does not work

| You see | Why | What to do |
|---|---|---|
| The webhook gets `401` | The signature does not match | The secret in the helpdesk and in `ZENDESK_WEBHOOK_SECRET` / `ZAMMAD_WEBHOOK_SECRET` must be the same, with no spaces. A proxy that changes the body breaks the signature |
| `404 unknown ticket system` | The URL path is wrong | It must end in `/webhooks/zendesk` or `/webhooks/zammad` |
| `422` from the webhook | The body is not a ticket | Compare it with the JSON in step 7. A placeholder holding quotes or line breaks can break the JSON your helpdesk renders: look at the raw request in the helpdesk's webhook log |
| `413` | The body is over 1 MB | Send less: the ticket text is cut to 10,000 characters anyway |
| `201`, but nothing appears in the helpdesk | Dry-run: no credentials | `doctor` says so. Set them (stage 3) and restart |
| Outbox `"mode": "error"`, 401 or 403 | The token or its permissions | `doctor --live`. Zendesk: an agent or admin. Zammad: `ticket.agent` |
| Outbox error 404 or 422 | The ticket id does not exist, or the account cannot touch that ticket (a Zammad group, for example) | Check the id in the helpdesk and the account's access to that group |
| An agent adds the tag and nothing is sent: the response says `"ignored": "decisions by tag need ..._WEBHOOK_SECRET to be set"` | A decision sends a reply, so it needs a verified signature | Set the webhook secret and restart |
| `"ignored": "reviewer is not allowed ..."` | The agent is not in `ALLOWED_REVIEWERS`, or the payload carries no agent | Add them (compare the email exactly as the helpdesk sends it), or fix the `agent` / `updated_by` field |
| `"ignored": "already decided"` | The ticket was already approved or rejected, or this is the helpdesk repeating itself | Expected |
| `"ignored": "both decision tags are set"` | The ticket has `ai_approve` and `ai_reject` | Remove one |
| `"ignored": "the ticket already carried a decision tag ..."` | The tag was on the ticket when it arrived | Remove it, wait for an update, add it again, or decide in the review page |
| `"ignored": "the draft makes a promise ..."` | The draft promises a refund or similar | Decide in the review page, where the promise is shown |
| The response is only `"duplicate": true` | The update has no decision tag in it | The decision trigger's body is missing `tags` (step 7.4), or the trigger fires before the tag is saved |
| After approval, a Zammad customer got no email | The reply is posted as a non-internal note, which does not email | Known limitation: see [limitations](limitations.md) |
| Drafts always say a specialist will reply | No article matched | Add the article or its `Keywords:` line (step 2) and check with `evaluate` |
| Everything is P1 or everything is P3 | The keyword rules do not fit your tickets | Compare with Claude on your export (step 3) |
| The queue is empty after a restart | The database is in memory or in a different directory | Set `TRIAGE_DB` to an absolute path; `doctor` shows which file is used |

If it is still unclear, open an issue with the output of `python -m support_copilot.doctor` (it contains no secrets) and the relevant lines of the log.
