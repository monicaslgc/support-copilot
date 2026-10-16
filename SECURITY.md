# Security

This is a concept project: it has not been security-reviewed or run against a live ticket system. Read this before connecting real customer data.

## What is in place

- Webhooks are verified with each system's signature (Zammad HMAC-SHA1, Zendesk HMAC-SHA256 with timestamp) when its secret is set.
- A decision made by tagging a ticket in the helpdesk is only accepted for a connected system when its webhook secret is set, because it sends a reply to a customer.
- The review API can require `X-API-Key` (`API_KEY`).
- Private details (emails, cards, IBANs, phones, passwords) are masked before text goes to the model.
- The review page never inserts ticket text as HTML.
- At startup the app logs a warning for every connected system that has no webhook secret, and when `API_KEY` is unset.

## Before connecting a real ticket system

1. Set `API_KEY` and the webhook secret of every connected system.
2. Put the app behind HTTPS and a reverse proxy; it does not terminate TLS or rate-limit.
3. Treat the SQLite file as customer data: it stores ticket text unmasked. Restrict and back it up accordingly.
4. Know that masking is rule-based and does not catch names or addresses. A person reads every reply, but the model sees the rest.

The full list of known gaps is in [limitations](docs/limitations.md).

## Reporting a vulnerability

Please report it privately rather than in a public issue: use "Report a vulnerability" on the repository's Security tab if it is enabled; if it is not, open an issue that only asks for a private way to reach the author, without any details.
