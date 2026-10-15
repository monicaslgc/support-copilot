import os

os.environ.pop("ANTHROPIC_API_KEY", None)   # tests are offline, rules mode
os.environ.pop("ZENDESK_WEBHOOK_SECRET", None)
os.environ["TRIAGE_DB"] = ":memory:"

import pytest

import app.main as main
from app.db import connect
from app.zendesk import ZendeskClient


@pytest.fixture(autouse=True)
def isolated_state():
    """Every test gets a fresh DB and a dry-run Zendesk client; nothing leaks between tests."""
    main.conn = connect(":memory:")
    main.zendesk = ZendeskClient()
    yield
    main.zendesk = ZendeskClient()
