"""`python -m support_copilot.doctor` and the environment settings of the reference server."""
import httpx
import pytest

from support_copilot import Copilot, Taxonomy, doctor
from support_copilot.ticketsystems import ZendeskSystem
from tests.fakes import FakeZammad, FakeZendesk
from tests.test_llm import GOOD, FakeAnthropic, block, reply


def statuses(checks):
    return {c.name: c.status for c in checks}


def detail(checks, name):
    return next(c.detail for c in checks if c.name == name)


def test_a_fresh_offline_setup_has_nothing_to_fix_but_things_to_look_at():
    checks = doctor.run_checks(Copilot())
    assert "fail" not in statuses(checks).values()
    assert statuses(checks)["knowledge base"] == "warn" and "sample articles" in detail(checks, "knowledge base")
    assert statuses(checks)["database"] == "warn" and "in memory" in detail(checks, "database")
    assert "dry-run" in detail(checks, "zendesk") and "dry-run" in detail(checks, "zammad")


def test_your_own_articles_and_database_file_make_those_checks_pass(tmp_path):
    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "refunds.md").write_text("# Refunds\nKeywords: refund\nHow refunds work.")
    checks = doctor.run_checks(Copilot(db=tmp_path / "t.db", kb=tmp_path / "kb"))
    assert statuses(checks)["knowledge base"] == "ok" and "1 articles" in detail(checks, "knowledge base")
    assert statuses(checks)["database"] == "ok" and "Back this file up" in detail(checks, "database")
    (tmp_path / "empty").mkdir()
    assert statuses(doctor.run_checks(Copilot(kb=tmp_path / "empty")))["knowledge base"] == "fail"


def test_a_connected_helpdesk_without_a_webhook_secret_or_an_api_key_is_a_failure(monkeypatch):
    monkeypatch.delenv("ZENDESK_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    cp = Copilot(systems={"zendesk": FakeZendesk().make()})
    s = statuses(doctor.run_checks(cp))
    assert s["zendesk webhook secret"] == "fail" and s["api key"] == "fail"
    monkeypatch.setenv("ZENDESK_WEBHOOK_SECRET", "s3cret")
    cp.api_key = "k"
    s = statuses(doctor.run_checks(cp))
    assert s["zendesk webhook secret"] == "ok" and s["api key"] == "ok"


@pytest.mark.parametrize("fake_cls,who", [(FakeZendesk, "a@acme.com"), (FakeZammad, "agent@example.com")])
def test_live_checks_the_credentials_with_a_read_only_call(fake_cls, who):
    fake = fake_cls()
    checks = doctor.run_checks(Copilot(systems={fake.name: fake.make()}), live=True)
    assert statuses(checks)[f"{fake.name} credentials"] == "ok" and who in detail(checks, f"{fake.name} credentials")
    assert [r.method for r in fake.requests] == ["GET"]                     # it read, and changed nothing
    assert not fake.tickets
    offline = doctor.run_checks(Copilot(systems={fake.name: fake_cls().make()}))
    assert "--live" in detail(offline, f"{fake.name} credentials")


def test_live_says_what_is_wrong_with_the_credentials():
    refused = ZendeskSystem("acme", "a@acme.com", "bad", transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    checks = doctor.run_checks(Copilot(systems={"zendesk": refused}), live=True)
    assert statuses(checks)["zendesk credentials"] == "fail" and "refused" in detail(checks, "zendesk credentials")
    unreachable = ZendeskSystem("acme", "a@acme.com", "t", transport=httpx.MockTransport(
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("no route"))))
    assert "could not reach" in detail(doctor.run_checks(Copilot(systems={"zendesk": unreachable}), live=True),
                                       "zendesk credentials")
    fz = FakeZendesk()
    fz.role = "end-user"
    checks = doctor.run_checks(Copilot(systems={"zendesk": fz.make()}), live=True)
    assert statuses(checks)["zendesk credentials"] == "fail" and "cannot update tickets" in detail(
        checks, "zendesk credentials")


def test_model_makes_one_call_and_reports_a_fallback_as_a_failure(monkeypatch):
    import sys
    fake = FakeAnthropic()
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    ok = doctor.run_checks(Copilot(), model=True)
    assert statuses(ok)["model call"] == "ok" and GOOD["category"] in detail(ok, "model call")
    fake.next_reply = reply(block("text", "not json at all"))
    bad = doctor.run_checks(Copilot(), model=True)
    assert statuses(bad)["model call"] == "fail" and "fell back to rules" in detail(bad, "model call")


def test_the_report_and_the_exit_code(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)                      # no triage.db here: the command must not create one
    for var in ("ANTHROPIC_API_KEY", "ZENDESK_SUBDOMAIN", "ZAMMAD_URL", "API_KEY", "KB_DIR", "TRIAGE_DB"):
        monkeypatch.delenv(var, raising=False)
    assert doctor.main([]) == 0
    out = capsys.readouterr().out
    assert "to look at." in out and "[warn] database" in out and not (tmp_path / "triage.db").exists()
    assert "only the default" in out and "set TRIAGE_DB" in out
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_EMAIL", "a@acme.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "t")
    assert doctor.main([]) == 1 and "[FAIL] zendesk webhook secret" in capsys.readouterr().out


def test_a_setting_that_cannot_work_is_reported_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setenv("CATEGORY_LABELS", "billing")
    assert doctor.main([]) == 1
    assert "CATEGORY_LABELS: 'billing' is not name=label" in capsys.readouterr().out


# ---------------- the environment settings the reference server reads
def test_the_tag_and_decision_settings_come_from_the_environment(monkeypatch):
    for var, value in {"TAG_PREFIX": "cp_", "APPROVE_TAG": "send", "REJECT_TAG": "discard", "HELPDESK_DECISIONS": "1",
                       "CATEGORY_LABELS": "billing=finance-queries, bug = defects", "TEAM_LABELS": "finance=accounts",
                       "ALLOWED_REVIEWERS": "ana@acme.com, rui@acme.com", "ALLOW_UNSIGNED_DECISIONS": "yes"}.items():
        monkeypatch.setenv(var, value)
    cp = Copilot.from_env(db=":memory:")
    assert cp.taxonomy == Taxonomy(tag_prefix="cp_", approve_tag="send", reject_tag="discard",
                                   category_labels={"billing": "finance-queries", "bug": "defects"},
                                   team_labels={"finance": "accounts"})
    assert cp.allowed_reviewers == {"ana@acme.com", "rui@acme.com"} and cp.allow_unsigned_decisions is True
    monkeypatch.setenv("HELPDESK_DECISIONS", "0")
    assert Copilot.from_env(db=":memory:").taxonomy.helpdesk_decisions is False


def test_the_defaults_when_nothing_is_set(monkeypatch):
    for var in ("TAG_PREFIX", "APPROVE_TAG", "REJECT_TAG", "HELPDESK_DECISIONS", "CATEGORY_LABELS", "TEAM_LABELS",
                "ALLOWED_REVIEWERS", "ALLOW_UNSIGNED_DECISIONS", "KB_DIR"):
        monkeypatch.delenv(var, raising=False)
    cp = Copilot.from_env(db=":memory:")
    assert cp.taxonomy == Taxonomy() and cp.allowed_reviewers is None and cp.allow_unsigned_decisions is False


@pytest.mark.parametrize("var,value", [("HELPDESK_DECISIONS", "maybe"), ("TEAM_LABELS", "finance"),
                                       ("CATEGORY_LABELS", "billing=Has Space"), ("TAG_PREFIX", "AI ")])
def test_a_bad_setting_stops_the_start_with_a_message(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValueError):
        Copilot.from_env(db=":memory:")


def test_kb_dir_points_the_reference_server_at_your_articles(monkeypatch, tmp_path):
    (tmp_path / "warp.md").write_text("# Warp drive\nKeywords: warp\nReplace the crystal.")
    monkeypatch.setenv("KB_DIR", str(tmp_path))
    assert [d["id"] for d in Copilot.from_env(db=":memory:").kb.docs] == ["warp"]


def test_the_database_check_follows_where_the_server_will_put_the_file(monkeypatch, tmp_path, capsys):
    for var in ("ANTHROPIC_API_KEY", "ZENDESK_SUBDOMAIN", "ZAMMAD_URL", "API_KEY", "KB_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "queue.db"))               # a fixed path in a real directory
    assert doctor.main([]) == 0
    assert "[ok  ] database" in capsys.readouterr().out and not (tmp_path / "queue.db").exists()
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "missing" / "queue.db"))   # a directory that does not exist
    assert doctor.main([]) == 1 and "[FAIL] database" in capsys.readouterr().out
