"""The demo script (scripts/demo.py) and the example payloads must keep working: they are what people try first."""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from support_copilot import main
from support_copilot.ticketsystems import ZammadSystem, ZendeskSystem

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "demo.py")
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)


# ---------------- the scenarios ----------------
@pytest.mark.parametrize("name", list(demo.SCENARIOS))
def test_each_scenario_passes_in_process(name):
    lines: list[str] = []
    assert demo.run([name, "--in-process"], out=lines.append) == 0, "\n".join(lines[-10:])
    assert lines[-1].strip() == "All 1 scenario passed."


def test_all_scenarios_pass_together():
    lines: list[str] = []
    assert demo.run(["all", "--in-process"], out=lines.append) == 0, "\n".join(lines[-10:])
    assert lines[-1].strip() == f"All {len(demo.SCENARIOS)} scenarios passed."


def test_scenarios_can_be_run_again_and_again_on_the_same_server():
    """A live server keeps its tickets between runs. Scenarios must not trip over the ones they left behind:
    this is the check that once caught a duplicate test being linked to another run's tickets."""
    client = demo.in_process_client()
    for _ in range(3):
        for fn, _stories in demo.SCENARIOS.values():
            fn(demo.Run(client, out=lambda *_: None))


def test_list_names_every_scenario_and_its_stories():
    lines: list[str] = []
    assert demo.run(["list"], out=lines.append) == 0
    assert len(lines) == len(demo.SCENARIOS)
    assert all(name in " ".join(lines) for name in demo.SCENARIOS)
    assert any("US-03" in line and line.startswith("outage") for line in lines)


def test_a_failed_check_stops_the_run_with_exit_code_1(monkeypatch):
    def broken(run):
        run.check(False, "a promise was let through")

    broken.__doc__ = "x: a scenario that fails."
    monkeypatch.setitem(demo.SCENARIOS, "triage", (broken, "US-01"))
    lines: list[str] = []
    assert demo.run(["triage", "--in-process"], out=lines.append) == 1
    assert "FAILED in 'triage': a promise was let through" in lines[-1]


def test_an_unreachable_server_exits_with_2_and_says_what_to_do():
    lines: list[str] = []
    assert demo.run(["triage", "--url", "http://127.0.0.1:9"], out=lines.append) == 2
    assert "is the server running?" in lines[-1]


def test_runs_get_distinct_ids_even_when_started_together():
    assert len({demo.Run(None).uid for _ in range(50)}) == 50


# ---------------- the example payloads ----------------
def test_example_payloads_are_valid_for_their_adapters():
    zammad = ZammadSystem().parse_webhook((ROOT / "examples" / "zammad_webhook.json").read_bytes())
    assert (zammad.external_id, zammad.subject) == ("1042", "Cannot export the monthly report")
    zendesk = ZendeskSystem().parse_webhook((ROOT / "examples" / "zendesk_webhook.json").read_bytes())
    assert (zendesk.external_id, zendesk.requester) == ("2201", "Sam Carter")


def test_the_examples_readme_flow_works_end_to_end():
    c = TestClient(main.app)
    for system in ("zammad", "zendesk"):
        body = (ROOT / "examples" / f"{system}_webhook.json").read_bytes()
        r = c.post(f"/webhooks/{system}", content=body, headers={"content-type": "application/json"})
        assert r.status_code == 201 and r.json()["suggestion"]["draft_reply"]
    notes = [o for o in c.get("/outbox").json() if o["kind"] == "triage_note"]
    assert {o["system"] for o in notes} == {"zammad", "zendesk"}                  # one internal note each
    reply = "Hi Rui, we are looking into the export problem."
    approved = c.post("/tickets/1/review", json={"action": "approved", "reviewer": "ana", "edited_reply": reply})
    assert approved.status_code == 200 and approved.json()["sync"] == "dry_run"
    assert any(o["kind"] == "public_reply" for o in c.get("/outbox").json())


def test_the_decision_examples_approve_the_tickets_the_creation_examples_made():
    c = TestClient(main.app)
    for system in ("zammad", "zendesk"):
        made = c.post(f"/webhooks/{system}", content=(ROOT / "examples" / f"{system}_webhook.json").read_bytes(),
                      headers={"content-type": "application/json"}).json()
        body = (ROOT / "examples" / f"{system}_decision.json").read_bytes()
        done = c.post(f"/webhooks/{system}", content=body, headers={"content-type": "application/json"}).json()
        assert done["decided_in"] == system and done["id"] == made["id"] and done["sync"] == "dry_run"
        review = c.get(f"/tickets/{made['id']}").json()["review"]
        assert (review["action"], review["reviewer"]) == ("approved", "ana@example.com")
        again = c.post(f"/webhooks/{system}", content=body, headers={"content-type": "application/json"}).json()
        assert again["ignored"] == "already decided"
    public = [o for o in c.get("/outbox").json() if o["kind"] == "public_reply"]
    assert {o["system"] for o in public} == {"zammad", "zendesk"} and len(public) == 2   # one reply each, no more


@pytest.mark.skipif(shutil.which("openssl") is None, reason="the README recipe uses openssl")
def test_the_signing_recipes_in_the_examples_readme_produce_valid_signatures(monkeypatch):
    """Run the openssl commands exactly as the README gives them and check the server accepts the result."""
    monkeypatch.setenv("ZAMMAD_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("ZENDESK_WEBHOOK_SECRET", "s3cret")
    readme = (ROOT / "examples" / "README.md").read_text()
    assert "openssl dgst -sha1 -hmac s3cret" in readme
    assert "openssl dgst -sha256 -hmac s3cret -binary | base64" in readme

    def sh(cmd):
        return subprocess.run(["bash", "-c", cmd], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()

    c = TestClient(main.app)
    z_sig = sh("openssl dgst -sha1 -hmac s3cret < examples/zammad_webhook.json | awk '{print $NF}'")
    body = (ROOT / "examples" / "zammad_webhook.json").read_bytes()
    ok = c.post("/webhooks/zammad", content=body, headers={"content-type": "application/json",
                                                           "X-Hub-Signature": f"sha1={z_sig}"})
    assert ok.status_code == 201
    assert c.post("/webhooks/zammad", content=body, headers={"content-type": "application/json"}).status_code == 401

    ts = "2026-10-16T10:00:00Z"
    d_sig = sh(f"( printf '%s' '{ts}'; cat examples/zendesk_webhook.json ) "
               "| openssl dgst -sha256 -hmac s3cret -binary | base64")
    body = (ROOT / "examples" / "zendesk_webhook.json").read_bytes()
    headers = {"content-type": "application/json", "X-Zendesk-Webhook-Signature-Timestamp": ts}
    assert c.post("/webhooks/zendesk", content=body, headers={**headers, "X-Zendesk-Webhook-Signature": d_sig}
                  ).status_code == 201
    assert json.loads(body)["ticket_id"] == "2201"
