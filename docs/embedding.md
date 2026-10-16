# Embedding guide

How to use support-copilot inside your own ecosystem instead of running it as a separate app. Back to the [README](../README.md). Connecting a helpdesk? [Run it against your environment](getting-started.md) covers the stages, the checks and troubleshooting.

Install from GitHub (it is not on PyPI), choosing the extras you need:

```bash
pip install "support-copilot @ git+https://github.com/monicaslgc/support-copilot"              # the core: pydantic and httpx
pip install "support-copilot[fastapi] @ git+https://github.com/monicaslgc/support-copilot"     # + the router
pip install "support-copilot[claude] @ git+https://github.com/monicaslgc/support-copilot"      # + model-backed triage
```

## The Copilot object

Everything is one object. It owns a database, a knowledge base and the ticket-system adapters, and holds no global state, so two of them can live in one process.

```python
from support_copilot import Copilot, Review, TicketIn

cp = Copilot(
    db="tickets.db",            # a path, ":memory:" (the default) or a sqlite3 connection you opened
    kb="our-articles/",         # a folder of markdown help articles; default is a small bundled sample
    systems=None,               # ticket-system adapters by name; default is Zendesk and Zammad from the environment
    api_key=None,               # X-API-Key for the router; default is the API_KEY environment variable
    taxonomy=None,              # your tags and names, and the tags that approve or reject (below)
    allowed_reviewers=None,     # emails who may decide by tag in the helpdesk; default is anyone who can edit the ticket
    allow_unsigned_decisions=False,  # tag decisions need the webhook secret for a connected system (see integrations)
)

suggestion = cp.suggest(TicketIn(subject=..., body=..., customer=...))   # triage + draft, nothing stored or sent
queued = cp.submit(ticket)                                               # the same, and queued for review
cp.review(queued["id"], Review(action="approved", reviewer="ana"))       # a person decides
```

| Method | What it does |
|---|---|
| `suggest(ticket)` | Category, priority, team, sentiment, language, the matching articles and a checked draft. No storage, no network except the model call if Claude is on. |
| `submit(ticket)` | `suggest` plus storing, linking to duplicates and incidents. Returns the id; the ticket waits for review. |
| `list_tickets(status, limit)`, `get_ticket(id)` | The queue, most urgent first, with the customer's history, the incident it belongs to and the review. |
| `review(id, Review(...))` | Records a person's decision. For tickets from a ticket system, an approved reply is posted to the customer; a rejection is not. |
| `list_clusters()`, `review_cluster(id, Review(...))` | Possible incidents, and one decision for all their pending tickets. |
| `handle_webhook(system, headers, raw_body)` | Verifies the signature, then either triages a new ticket (queue, internal note, tags) or, for a ticket it already holds, acts on an `ai_approve` / `ai_reject` tag. Idempotent per ticket. Blocking: use a worker thread in async code. |
| `metrics()`, `metrics_breakdown()`, `outbox()` | How often people agree with the AI per category, and the audit log of calls to ticket systems. |

Refusals (unknown ticket, already reviewed, bad signature) raise `CopilotError` with an HTTP-style `status`, so your own code or API can map them.

## In your FastAPI app

```python
from fastapi import FastAPI
from support_copilot.api import create_router

app = FastAPI()
app.include_router(create_router(cp), prefix="/copilot")     # /copilot/tickets, /copilot/webhooks/zendesk, ...
```

The router carries the review API, the incident and metrics endpoints and the webhooks (see [API](api.md)). Point your ticket system's webhook at `/copilot/webhooks/<system>`. Review routes need `X-API-Key` when a key is set; put your own authentication in front of the router if you need user-level access. The reference review page is `support_copilot/static/index.html`: it expects the routes at the root, so serve it there or build your own UI on the same routes.

Not using FastAPI? Call the `Copilot` methods from any framework: that is all the router does.

## Your tags, names and who may decide

Tags are how the add-on talks to the rest of your helpdesk: your triggers, views and reports can use them without it knowing your groups, ids or custom fields.

```python
from support_copilot import Copilot, Taxonomy

cp = Copilot(
    taxonomy=Taxonomy(
        tag_prefix="copilot_",                              # copilot_triaged, copilot_cat_..., copilot_team_...
        category_labels={"billing": "finance-queries"},     # our category name -> yours (two may share one)
        team_labels={"finance": "accounts"},
        approve_tag="send", reject_tag="discard",           # copilot_send sends the reply, copilot_discard rejects
    ),
    allowed_reviewers={"ana@acme.com", "rui@acme.com"},     # only these people's tags decide
)
```

Labels must be valid helpdesk tags (lowercase letters, digits, `_`, `-`), checked when the object is built. The defaults are the `ai_` tags used throughout these docs. The categories and teams the AI chooses between are still ours; the labels rename them. Deciding from the helpdesk is described in [integrations](integrations.md#deciding-from-inside-the-helpdesk).

## Your own help articles

Give `kb` a folder of `.md` files. The first line is the title, and an optional `Keywords:` line lists words customers use that the article itself does not. Retrieval is lexical TF-IDF: if you have embeddings, replace `KnowledgeBase.search` (same signature) and pass your own `KnowledgeBase` instance.

## Your own ticket system

Subclass `TicketSystem` (see [integrations](integrations.md)) and pass it in: `Copilot(systems={"helpdesk": MyHelpdesk()})`. The rules that matter are in the base class: internal notes before approval, tags added never replaced, a public reply only after a person approves.

## What is configurable and what is not

- In code: database, knowledge base, ticket systems, API key, tags and names, who may decide.
- By environment variable (see [API and configuration](api.md)): the model, thresholds for incidents and for automation candidates, ticket-system credentials and webhook secrets. These are read at call time and are process-wide.
- Not yet: the storage backend (SQLite only, behind no interface), the model provider (Claude only; without a key the rules run), and the guardrail rules.

Before pointing it at real customer data, read [SECURITY](../SECURITY.md).
