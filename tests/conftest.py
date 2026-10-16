import os

os.environ.pop("ANTHROPIC_API_KEY", None)   # tests are offline, rules mode
for var in ("ZENDESK_WEBHOOK_SECRET", "ZAMMAD_WEBHOOK_SECRET"):
    os.environ.pop(var, None)
os.environ["TRIAGE_DB"] = ":memory:"

import pytest

from app import state
from app.db import connect
from app.ticketsystems import build_systems


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    """Every test gets a fresh DB and dry-run adapters (no credentials); nothing leaks between tests."""
    for var in ("ZENDESK_SUBDOMAIN", "ZENDESK_EMAIL", "ZENDESK_API_TOKEN", "ZAMMAD_URL", "ZAMMAD_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    state.conn = connect(":memory:")
    state.systems = build_systems()
    yield
