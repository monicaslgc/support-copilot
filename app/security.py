"""Who may call the API, and what to warn about when it is left open."""
import hmac
import os

from fastapi import Header, HTTPException

from . import state
from .llm import log


def require_api_key(x_api_key: str | None = Header(default=None)):
    """Protects the review/ops API. Webhooks are authenticated by each system's own signature instead.

    If API_KEY is not set the API is open (local development): set it anywhere reachable by others.
    """
    expected = os.getenv("API_KEY")
    if expected and not hmac.compare_digest((x_api_key or "").encode(), expected.encode()):
        raise HTTPException(401, "missing or invalid X-API-Key")


def security_warnings() -> list[str]:
    """Settings that leave a live deployment open. Logged at startup; only matters once a ticket system is
    connected, because that is when real customer data starts to flow."""
    systems = state.systems
    out = [f"{name} is connected but {s.secret_env} is not set: anyone who can reach this server can post "
           f"tickets to its webhook" for name, s in systems.items() if s.configured and not os.getenv(s.secret_env)]
    if any(s.configured for s in systems.values()) and not os.getenv("API_KEY"):
        out.append("API_KEY is not set: the review API, which returns customer messages, is open")
    return out


def log_startup_warnings() -> None:
    for w in security_warnings():
        log.warning(w)
