"""support-copilot as a library: usable without the server, in someone else's app, with their own settings."""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from support_copilot import Copilot, CopilotError, KnowledgeBase, Review, TicketIn
from support_copilot.api import create_router
from tests.fakes import FakeZendesk

ROOT = Path(__file__).resolve().parent.parent
T = TicketIn(subject="Cannot reset my password", body="The reset link never arrives", customer="Ana")


def run_python(code_or_file: str, *args: str, cwd=ROOT, **env):
    full_env = {**os.environ, "PYTHONPATH": str(ROOT), **env}
    full_env.pop("ANTHROPIC_API_KEY", None)
    return subprocess.run([sys.executable, *args, code_or_file], cwd=cwd, env=full_env, capture_output=True, text=True)


def test_the_core_needs_no_web_framework():
    code = ("import sys; sys.modules['fastapi'] = None; sys.modules['uvicorn'] = None\n"
            "from support_copilot import Copilot, TicketIn\n"
            "s = Copilot().suggest(TicketIn(subject='Invoice wrong', body='charged twice', customer='Ana'))\n"
            "print(s.triage.category.value)")
    done = run_python(code, "-c")
    assert done.returncode == 0, done.stderr


def test_the_library_example_runs_and_ends_with_a_recorded_decision():
    done = run_python("examples/library_usage.py")
    assert done.returncode == 0, done.stderr
    assert "decision recorded: approved" in done.stdout


def test_the_embedding_example_mounts_the_router_under_the_hosts_own_app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the example keeps its database in ./tickets.db
    monkeypatch.syspath_prepend(str(ROOT))
    host = importlib.reload(importlib.import_module("examples.embed_fastapi"))
    c = TestClient(host.app)
    assert c.get("/healthz").json() == {"ok": True}
    r = c.post("/copilot/tickets", json={"subject": T.subject, "body": T.body, "customer": "Ana"})
    assert r.status_code == 201 and c.get("/copilot/tickets").json()[0]["id"] == r.json()["id"]
    assert c.post("/copilot/webhooks/nosuch", json={}).status_code == 404
    assert (tmp_path / "tickets.db").exists()
    host.copilot.conn.close()


def test_suggest_stores_nothing_and_submit_queues_for_review():
    cp = Copilot()
    cp.suggest(T)
    assert cp.list_tickets() == []
    queued = cp.submit(T)
    assert [t["id"] for t in cp.list_tickets(status="pending_review")] == [queued["id"]]


def test_two_copilots_in_one_process_do_not_share_anything():
    a, b = Copilot(), Copilot()
    a.submit(T)
    assert len(a.list_tickets()) == 1 and b.list_tickets() == []


def test_your_own_knowledge_base_folder_is_used(tmp_path):
    (tmp_path / "warp-drive.md").write_text("# Warp drive\nKeywords: warp, engine, dilithium\nReplace the crystal.")
    cp = Copilot(kb=tmp_path)
    s = cp.suggest(TicketIn(subject="Warp engine down", body="the dilithium crystal cracked", customer="Kirk"))
    assert [a["id"] for a in s.articles] == ["warp-drive"]
    assert isinstance(Copilot(kb=KnowledgeBase(tmp_path)).kb, KnowledgeBase)


def test_the_queue_lives_in_the_database_you_give_it(tmp_path):
    path = tmp_path / "queue.db"
    first = Copilot(db=path)
    tid = first.submit(T)["id"]
    first.conn.close()
    assert Copilot(db=path).get_ticket(tid)["subject"] == T.subject


def test_a_sqlite_connection_you_opened_gets_its_tables_created():
    import sqlite3
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    cp = Copilot(db=conn)
    assert cp.conn is conn and cp.submit(T)["id"] == 1


def test_the_api_key_can_be_set_in_code_and_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("API_KEY", "from-env")
    app = FastAPI()
    app.include_router(create_router(Copilot(api_key="from-code")))
    c = TestClient(app)
    assert c.get("/tickets").status_code == 401
    assert c.get("/tickets", headers={"X-API-Key": "from-env"}).status_code == 401
    assert c.get("/tickets", headers={"X-API-Key": "from-code"}).status_code == 200


def test_refusals_are_typed_errors_the_host_can_catch():
    cp = Copilot()
    with pytest.raises(CopilotError) as e:
        cp.get_ticket(99)
    assert e.value.status == 404
    tid = cp.submit(T)["id"]
    cp.review(tid, Review(action="approved", reviewer="ana"))
    with pytest.raises(CopilotError) as e:
        cp.review(tid, Review(action="approved", reviewer="rui"))
    assert e.value.status == 409


def test_your_own_ticket_system_adapter_can_be_injected():
    fz = FakeZendesk()
    fz.seed(7)
    cp = Copilot(systems={"zendesk": fz.make()})
    hook = (b'{"ticket_id": "7", "subject": "Duplicate charge on invoice",'
            b' "description": "refund my invoice please"}')
    done = cp.handle_webhook("zendesk", {}, hook)
    assert fz.internal(7) and fz.public(7) == []  # an internal note only, until a person approves
    cp.review(done["id"], Review(action="approved", reviewer="ana"))
    assert len(fz.public(7)) == 1


def test_the_package_ships_its_sample_articles_and_review_page():
    pkg = ROOT / "support_copilot"
    assert list((pkg / "kb").glob("*.md")) and (pkg / "static" / "index.html").is_file()
    assert "[tool.setuptools.package-data]" in (ROOT / "pyproject.toml").read_text()
