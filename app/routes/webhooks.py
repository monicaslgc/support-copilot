from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from .. import ingest, state

MAX_WEBHOOK_BYTES = 1_000_000

# No API key here: each ticket system authenticates its webhooks with its own signature.
router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/{system_name}", status_code=201)
async def ticket_system_webhook(system_name: str, request: Request):
    """Inbound webhook from a ticket system (zendesk, zammad). Configure it on 'ticket created'."""
    system = state.systems.get(system_name)
    if not system:
        raise HTTPException(404, f"unknown ticket system '{system_name}'")
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "webhook body too large")
    # The model and ticket-system calls block for seconds: keep them off the event loop.
    return await run_in_threadpool(ingest.receive_webhook, system, request.headers, raw)
