# Engineering notes

How it is tested and how it behaves as the queue grows. Back to the [README](../README.md).

## Tests

470 tests, run on Python 3.11, 3.12 and 3.13 in CI together with `ruff` and `mypy`, plus a job that installs the built wheel in a clean environment and runs the library example. The review page was also driven in a real browser (Chromium) to check the approve and reject flows, the mobile layout and that a hostile ticket (`<script>`, `<img onerror>`) is shown as text and never executed; that browser run is a manual check, not part of CI.

- **End to end, for every ticket system:** the same scenarios run against stateful fakes of the Zammad and Zendesk APIs that apply each system's update semantics (additive tags, public vs internal), and assert the final state of the ticket, not just the requests sent.
- **The Claude path:** a fake SDK checks the request shape (model, low effort, enough `max_tokens` for thinking, timeout, fenced ticket text, no removed parameters) and every kind of unusable reply (refusal, truncation, thinking block first, not JSON, unknown category, missing keys, API error).
- **Concurrency:** 8 threads reviewing the same ticket (exactly one wins, the customer gets one reply), 8 identical webhooks (one ticket, one note, no 500s), and a deterministic version of the same race.
- **User stories:** one acceptance test per story in [docs/user-stories.md](user-stories.md), and a check that every story has its test and every test its story.
- **The demo and the examples:** `scripts/demo.py` runs every scenario in process, and again and again on the same app (a live server keeps its tickets, and this caught a scenario that was being linked to another run's tickets). The example payloads, the README flow and the `openssl` signing commands are run too, so the first things a newcomer tries cannot rot.
- **The review page:** `tests/test_ui.py` checks the colour tokens, that every text and background pair keeps WCAG AA contrast in light and dark, that ticket text is never inserted as HTML, that nothing is loaded from outside, that ticket, AI suggestion and human decision are separate, and that the approval controls and the empty, loading and error states exist. It reads the page source: it does not run the JavaScript.
- **Security and limits:** API key on every protected route, webhook signatures, oversized webhooks, review actions that are not approved/rejected.

I checked that the tests catch real bugs by breaking the code on purpose in more than 20 ways (public/internal swapped, tags overwritten, wrong priority map, wrong signature algorithm, missing idempotency, ids colliding across systems, non-atomic review, unlocked database reads, reading `content[0]` instead of the text block, the old `max_tokens`, a 10 minute timeout, unhandled refusals, no API key check, no size limit): each break made at least one test fail.

## Performance

`python scripts/benchmark.py` measures it: 2000 tickets in the same hour, in process (no network, no model), one SQLite connection, rules mode. On a 4-CPU machine with Python 3.13 and SQLite 3.45 it gave, over three runs, a median of about 1 ms to save a ticket while the queue holds the first 100, and about 3 to 4 ms from 500 tickets up to 2000: it rises while the queue fills the one-hour window and then stays flat, instead of growing with the queue. Listing the 100 most urgent tickets took about 3 to 5 ms and the 500 most urgent about 13 to 21 ms. Your machine will differ, so the script prints its versions with its numbers.

Why it stays flat: the rules that link related tickets look up candidates with indexes (one row by topic, one by customer) and only compare near-identical text among the latest 200 tickets, and the queue is sorted and limited in SQL. The earlier version scanned every recent ticket on each insert while holding the lock that serialises all database access, and `tests/test_scale.py` checks the query plans so that cannot come back. With a real model the call to it dominates, not the database.

A reasonable ceiling for this design is one instance and a few thousand tickets an hour. Beyond that, move to PostgreSQL, run the model calls and ticket-system pushes in a background queue, and make bulk reviews asynchronous: a bulk decision on a big incident currently pushes to the ticket system one ticket at a time inside one request.

At startup the app logs a warning when a ticket system is connected but its webhook secret, or `API_KEY`, is not set, since that leaves the endpoints open.
