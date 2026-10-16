"""How long saving a ticket and listing the queue take as the queue grows. Reproduces docs/engineering.md.

    python scripts/benchmark.py                 # 2000 tickets, all in the same hour, rules mode, in memory
    python scripts/benchmark.py --tickets 500

No network and no model: it measures the database, the linking rules and the triage rules on one SQLite
connection. The numbers depend on the machine, so it prints the Python and SQLite versions with them.
"""
import argparse
import os
import platform
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TOPICS = [
    "I was charged twice on my invoice this month and I want a refund",
    "Cannot export the monthly report to CSV, it fails every time",
    "The password reset email never arrives and I am locked out",
    "The platform is down for everyone, total outage since this morning",
    "I get an error and the page crashes when saving a contact",
    "How do I add a new user and set their permissions",
]


def measure(tickets: int) -> tuple[dict[int, float], float, float]:
    from support_copilot import Copilot, TicketIn

    cp = Copilot(db=":memory:")
    times = []
    for i in range(tickets):
        ticket = TicketIn(subject=f"Ticket {i}: {TOPICS[i % 6][:40]}",
                          body=f"{TOPICS[(i * 7) % 6]}. Order {i}. Please help.",
                          customer=f"customer{i % 700}")
        start = time.perf_counter()
        cp.submit(ticket)
        times.append((time.perf_counter() - start) * 1000)
    blocks = {n: statistics.median(times[n - 100:n]) for n in (100, tickets // 4, tickets // 2, tickets) if n >= 100}
    start = time.perf_counter()
    cp.list_tickets(limit=100)
    list100 = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    cp.list_tickets(limit=500)
    return blocks, list100, (time.perf_counter() - start) * 1000


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tickets", type=int, default=2000, help="how many tickets to save (at least 100)")
    args = ap.parse_args(argv)
    if args.tickets < 100:
        raise SystemExit("--tickets must be at least 100")
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        os.environ.pop(var, None)
    blocks, list100, list500 = measure(args.tickets)
    print(f"python {platform.python_version()}, sqlite {sqlite3.sqlite_version}, {os.cpu_count()} cpus")
    print("median time to save a ticket, over the 100 tickets ending at ticket N:")
    for n, ms in blocks.items():
        print(f"  N = {n:>5}: {ms:5.1f} ms")
    print(f"listing the 100 most urgent tickets: {list100:.0f} ms; the 500 most urgent: {list500:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
