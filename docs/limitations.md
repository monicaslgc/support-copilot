# Limitations and next steps

What is not verified, what is a baseline, and what comes next. Back to the [README](../README.md).

## Known limitations

- Not verified against live Zammad or Zendesk instances (see the status note in the README). That includes the payloads that carry tags and the acting agent for decisions made in the helpdesk.
- A reply approved by tag in the helpdesk is sent exactly as drafted; editing it first needs the review page or the API. Who may tag is whoever can edit the ticket unless `allowed_reviewers` is set.
- Retrieval is TF-IDF over six sample articles, with plural folding and a cutoff relative to the best match. It is lexical: it does not understand synonyms (unless an article lists them under `Keywords:`), so a differently worded question can miss the right article, and a ticket that is mostly emotion can match two weak articles. Embeddings (for example pgvector) are the next step.
- The rule-based sentiment and language detection is a small word list, not a model: it misses sarcasm and subtle anger, and short or mixed-language text is often `und`. Treat it as a baseline.
- Rule-based classification is a baseline, not a production classifier.
- Zammad: an approved reply is created as a non-internal `note` article, which the customer can see in the web portal. Zammad only emails the customer for an `email` article with a `to` address, and the adapter does not set those (the customer's address is not stored yet), so the customer may not get an email. Zendesk's public comment does notify the requester. Not verified against a live Zammad.
- No automatic retry for failed ticket-system calls: the decision is saved, the review page shows the failure in red, and `/outbox` holds the error and the request. `POST /outbox/{id}/retry` sends it again on demand; if several calls were recorded together, all are repeated (a second internal note, for example). Entries from before this version have no recorded request and cannot be retried.
- The webhook timestamp age is checked for Zendesk only (`WEBHOOK_MAX_AGE_SECONDS`); Zammad's signature does not include a timestamp. Within the window a replay is still possible, though a re-sent ticket changes nothing.
- A multi-request update (for example Zammad's priority + note + tags) is not transactional: if one call fails midway, the earlier ones stay applied.
- The webhook body is capped at 1 MB in the app; also limit the request size in your reverse proxy, which sees it first.
- Customers are compared by name as the helpdesk sends it (case and spacing ignored): two different people with the same name count as one, and one person who writes under two names counts as two.
- A single SQLite connection behind a lock: fine for one instance, not for several. Use PostgreSQL to scale out.
- The API key is one shared secret, not per-user auth, so reviews are attributed to whatever `reviewer` string the caller sends.

## Next steps

- Run the Zammad and Zendesk adapters against real instances and fix whatever differs from the documentation: the webhook payloads that carry tags and the acting agent, and the type of the article that carries a Zammad reply (so the customer is emailed)
- Let an agent edit the reply before approving from the helpdesk, not only approve or reject it as drafted
- Retry failed ticket-system calls automatically, not only on demand
- Run the Claude path against the live API and compare the model with the rules on a labelled evaluation set
- Feed the correction reasons back into the prompts and into that evaluation set
- Embeddings (for example pgvector) and PostgreSQL instead of TF-IDF and SQLite
- A background queue for model calls and ticket-system pushes, so a bulk review of a big incident is not one long request
- A release on PyPI (today it installs from GitHub)
- Build and run the Dockerfile and compose file on a machine with Docker (they are written and statically checked, but have not been built)
