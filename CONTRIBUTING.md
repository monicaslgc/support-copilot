# Contributing

Thanks for looking. This is a small, concept-stage project, and changes are welcome.

## Set up

```bash
make install   # Python 3.11+, `pip install -e ".[dev]"`
make check     # ruff and the full offline test suite: this is what CI runs
make run       # review page at http://127.0.0.1:8000/
```

No API key or ticket-system account is needed for any of it.

## Where things are

| Path | What lives there |
|---|---|
| `support_copilot/copilot.py` | The `Copilot` class: triage, the queue, the human decision, webhooks. No web framework, no global state |
| `support_copilot/api.py` | The FastAPI router over a `Copilot`; `main.py` is the reference server around it |
| `support_copilot/ticketsystems/` | One adapter per ticket system behind a shared interface |
| `support_copilot/{classifier,drafting,guardrails,clusters,feedback,llm}.py` | The rules and the model calls |
| `tests/` | Offline tests: stateful fakes of the ticket systems, a fake Anthropic SDK |
| `docs/` | Documentation; `tests/test_docs.py` checks its links and the numbers it quotes |

## Ground rules

- **A person approves every customer-facing message.** Anything that could send text to a customer before approval needs a test showing it cannot.
- **Add a test with every change.** If you change the number of tests, update the count quoted in the README and docs (a test tells you where).
- **Say what is unverified.** Do not claim a live ticket system or the live API works until it has been run against one.
- **Add a ticket system** by implementing `TicketSystem` (see [integrations](docs/integrations.md)) and running the shared flow tests against a fake of it.

Run `make check` before opening a pull request.
