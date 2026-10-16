"""python -m support_copilot.evaluate: run the triage over an export of your own tickets, offline."""
import csv
import json
from pathlib import Path

import pytest

from support_copilot import evaluate

ROOT = Path(__file__).resolve().parent.parent

ROWS = [
    {"subject": "Duplicate charge on invoice", "body": "I was charged twice, I want a refund.",
     "customer": "Sam", "category": "billing", "priority": "P3"},
    {"subject": "Platform is down", "body": "Nobody can access anything, total outage since 9am.",
     "customer": "Ana", "category": "bug", "priority": "P1"},
    {"subject": "Login", "body": "I forgot my password", "customer": "Rui", "category": "billing", "priority": "P1"},
]


@pytest.fixture(autouse=True)
def rules_only(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_the_sample_tickets_are_summarised(capsys):
    assert evaluate.main([str(ROOT / "data" / "sample_tickets.json")]) == 0
    out = capsys.readouterr().out
    assert "6 tickets, triaged by rules" in out and "by category:" in out and "by priority:" in out
    assert "a help article was found for" in out and "the first 3 drafts:" in out


def test_agreement_with_what_your_agents_decided_is_reported(tmp_path, capsys):
    write_csv(tmp_path / "t.csv", ROWS)
    evaluate.main([str(tmp_path / "t.csv"), "--preview", "0"])
    out = capsys.readouterr().out
    assert "category: agrees with your agents on 67% of 3 tickets" in out          # 2 of 3
    assert "priority: agrees with your agents on 67% of 3 tickets" in out
    assert "your agents said billing, the triage said access: 1" in out            # and where it differs
    assert "drafts:" not in out


def test_without_expected_columns_there_is_no_agreement_line(tmp_path, capsys):
    plain = [{"subject": r["subject"], "body": r["body"]} for r in ROWS]
    (tmp_path / "t.json").write_text(json.dumps(plain))
    evaluate.main([str(tmp_path / "t.json"), "--preview", "0"])
    assert "agrees with your agents" not in capsys.readouterr().out


def test_json_lines_and_limit_and_the_csv_of_results(tmp_path, capsys):
    (tmp_path / "t.jsonl").write_text("\n".join(json.dumps(r) for r in ROWS))
    out_csv = tmp_path / "results.csv"
    evaluate.main([str(tmp_path / "t.jsonl"), "--limit", "2", "--out", str(out_csv), "--preview", "0"])
    assert "2 tickets" in capsys.readouterr().out
    rows = list(csv.DictReader(out_csv.open(encoding="utf-8")))
    assert [r["category"] for r in rows] == ["billing", "bug"] and rows[1]["priority"] == "P1"
    assert rows[0]["draft"].startswith("Hi Sam") and rows[0]["article"] == "invoices"


def test_tickets_that_a_helpdesk_would_send_are_fitted_not_rejected(tmp_path, capsys):
    odd = [{"subject": "", "body": "x" * 20_000}, {"subject": "Hi", "body": ""}]
    (tmp_path / "t.json").write_text(json.dumps(odd))
    assert evaluate.main([str(tmp_path / "t.json"), "--preview", "0"]) == 0
    assert "tickets cut to 10,000 characters: 1" in capsys.readouterr().out


def test_your_own_articles_are_used(tmp_path, capsys):
    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "warp.md").write_text("# Warp drive\nKeywords: warp, dilithium\nReplace the crystal.")
    ticket = {"subject": "Warp drive down", "body": "the dilithium crystal cracked"}
    (tmp_path / "t.json").write_text(json.dumps([ticket]))
    out_csv = tmp_path / "r.csv"
    evaluate.main([str(tmp_path / "t.json"), "--kb", str(tmp_path / "kb"), "--out", str(out_csv), "--preview", "0"])
    assert next(csv.DictReader(out_csv.open()))["article"] == "warp"


def test_a_file_that_is_not_a_ticket_export_says_what_is_wrong(tmp_path):
    (tmp_path / "t.csv").write_text("title,text\nHello,World\n")
    with pytest.raises(SystemExit, match="every ticket needs a subject and a body column"):
        evaluate.main([str(tmp_path / "t.csv")])
    (tmp_path / "e.json").write_text("[]")
    with pytest.raises(SystemExit, match="no tickets"):
        evaluate.main([str(tmp_path / "e.json")])
    (tmp_path / "n.json").write_text('{"a": 1}')
    with pytest.raises(SystemExit, match="expected a list of tickets"):
        evaluate.main([str(tmp_path / "n.json")])


def test_rules_never_calls_the_model_even_with_a_key(monkeypatch, tmp_path, capsys):
    import sys

    from tests.test_llm import FakeAnthropic
    fake = FakeAnthropic()
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    (tmp_path / "t.json").write_text(json.dumps([{"subject": "Invoice question", "body": "my invoice is wrong"}]))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    evaluate.main([str(tmp_path / "t.json"), "--preview", "0"])
    captured = capsys.readouterr()
    assert "Claude is on" in captured.err and "triaged by llm" in captured.out and fake.calls
    fake.calls.clear()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    evaluate.main([str(tmp_path / "t.json"), "--preview", "0", "--rules"])
    assert not fake.calls and "triaged by rules" in capsys.readouterr().out
