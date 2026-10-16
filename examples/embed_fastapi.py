"""Mount support-copilot inside your own FastAPI app, next to your own routes.

    uvicorn examples.embed_fastapi:app --reload     # then open http://127.0.0.1:8000/docs
"""
from fastapi import FastAPI

from support_copilot import Copilot
from support_copilot.api import create_router

# kb="your-articles/" uses your own help articles; api_key="..." protects the review API
copilot = Copilot(db="tickets.db")

app = FastAPI(title="My support platform")


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.include_router(create_router(copilot), prefix="/copilot")  # /copilot/tickets, /copilot/webhooks/zendesk, ...
