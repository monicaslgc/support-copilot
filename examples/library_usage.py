"""Use support-copilot as a plain library: no web server, no ticket system, no API key.

    python examples/library_usage.py
"""
from support_copilot import Copilot, Review, TicketIn

# db="tickets.db" keeps the queue between runs; kb="your-articles/" uses your own help articles
cp = Copilot(db=":memory:")

# 1. Triage and draft. Nothing is stored or sent.
ticket = TicketIn(subject="Duplicate charge on my invoice", body="Please refund the second payment.", customer="Ana")
suggestion = cp.suggest(ticket)
print(f"{suggestion.triage.category.value} / {suggestion.triage.priority.value} -> team {suggestion.triage.team}")
print(suggestion.draft_reply)

# 2. Queue it for a person. Your own code decides how the agent sees the queue.
queued = cp.submit(ticket)
print("waiting for review:", [t["id"] for t in cp.list_tickets(status="pending_review")])

# 3. A person approves (optionally editing the reply). Only now would a connected ticket system be told.
result = cp.review(queued["id"], Review(action="approved", reviewer="agent@example.com"))
print("decision recorded:", result["status"].value)
