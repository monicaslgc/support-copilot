import os

os.environ.pop("ANTHROPIC_API_KEY", None)   # tests are offline, rules mode
for var in ("ZENDESK_WEBHOOK_SECRET", "ZAMMAD_WEBHOOK_SECRET"):
    os.environ.pop(var, None)
os.environ["TRIAGE_DB"] = ":memory:"

import pytest

from support_copilot import main
from support_copilot.db import connect
from support_copilot.ticketsystems import build_systems


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    """Every test gets a fresh DB and dry-run adapters (no credentials); nothing leaks between tests."""
    for var in ("ZENDESK_SUBDOMAIN", "ZENDESK_EMAIL", "ZENDESK_API_TOKEN", "ZAMMAD_URL", "ZAMMAD_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    main.copilot.conn = connect(":memory:")
    main.copilot.systems = build_systems()
    main.copilot.allow_unsigned_decisions = True   # the secure default has its own tests (test_helpdesk_decisions.py)
    yield
