"""The reference server: one Copilot, the review page and the router, ready to run.

    uvicorn support_copilot.main:app

To use support-copilot inside your own app, import `Copilot` and `create_router` instead (docs/embedding.md).
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from . import llm
from .api import create_router
from .copilot import Copilot

copilot = Copilot(db=None)  # None: TRIAGE_DB, else ./triage.db
app = FastAPI(title="support-copilot (human-in-the-loop ticket triage)")
copilot.log_startup_warnings()


@app.get("/", include_in_schema=False)
def review_ui():
    """The review queue page. It holds no data itself: it calls the (API-key protected) API."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "llm": llm.enabled(), "model": llm.model_name() if llm.enabled() else None,
            "ticket_systems": {n: s.configured for n, s in copilot.systems.items()}}


app.include_router(create_router(copilot))
