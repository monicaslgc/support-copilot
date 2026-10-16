"""support-copilot: the app is assembled here. The logic lives in the modules it wires together:

    routes/      the HTTP endpoints, one module per topic
    ingest.py    a ticket comes in: triage, draft, store, link, tell the ticket system
    reviews.py   a human decision on a ticket
    queries.py   what a ticket needs next to it when it is shown
    state.py     the database, the knowledge base and the ticket-system adapters
    security.py  the API key and the startup warnings
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from . import llm, security, state
from .routes import clusters, metrics, tickets, webhooks

app = FastAPI(title="support-copilot (human-in-the-loop ticket triage)")

security.log_startup_warnings()


@app.get("/", include_in_schema=False)
def review_ui():
    """The review queue page. It holds no data itself: it calls the (API-key protected) API."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "llm": llm.enabled(), "model": llm.model_name() if llm.enabled() else None,
            "ticket_systems": {n: s.configured for n, s in state.systems.items()}}


for module in (tickets, webhooks, clusters, metrics):
    app.include_router(module.router)
