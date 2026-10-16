"""Check a deployment before a helpdesk is pointed at it.

    python -m support_copilot.doctor                 # configuration only: nothing leaves this machine
    python -m support_copilot.doctor --live          # also call each connected helpdesk once, read-only
    python -m support_copilot.doctor --model         # also make one small call to Claude

It reads the same environment variables as the reference server. If you embed the library with your own
settings, run the same checks on your object: `run_checks(my_copilot, live=True)`.

`--live` only reads ("who am I" on the helpdesk): it creates, changes and sends nothing.
"""
import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__, kb, llm
from .copilot import Copilot


@dataclass(frozen=True)
class Check:
    status: str  # "ok", "warn" (works, but look at it) or "fail" (will not work)
    name: str
    detail: str


def _count_tickets_read_only(path: str) -> int | None:
    """The tickets in an existing database file, without opening it for writing or migrating it."""
    try:
        conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def run_checks(cp: Copilot, live: bool = False, model: bool = False, planned_db: str | None = None,
               existing_db: str | None = None) -> list[Check]:
    out: list[Check] = []
    add = lambda status, name, detail: out.append(Check(status, name, detail))  # noqa: E731

    py = sys.version_info
    add("ok" if py >= (3, 11) else "fail", "python", f"{py.major}.{py.minor}.{py.micro}, support-copilot {__version__}")

    path = (cp.conn.execute("PRAGMA database_list").fetchone()["file"] or "")
    count = cp.fetch_one("SELECT COUNT(*) AS n FROM tickets")["n"]
    if existing_db:  # looked at read-only: a check must not change the file it is checking
        found = _count_tickets_read_only(existing_db)
        if found is None:
            add("fail", "database", f"{existing_db} exists but cannot be read as a support-copilot database")
        else:
            add("ok", "database", f"{Path(existing_db).resolve()}: {found} tickets. Back this file up: it holds "
                                  "ticket text and every decision")
    elif path:
        add("ok", "database", f"{path}: {count} tickets. Back this file up: it holds ticket text and every decision")
    elif planned_db:  # the file does not exist yet: the server creates it, if it can
        where = Path(planned_db).resolve()
        if not where.parent.is_dir() or not os.access(where.parent, os.W_OK):
            add("fail", "database", f"{where} cannot be created: {where.parent} does not exist or is not writable")
        elif os.getenv("TRIAGE_DB"):
            add("ok", "database", f"{where} does not exist yet: the server creates it on first start")
        else:
            add("warn", "database", f"{where} does not exist yet, and it is only the default (relative to the "
                                    "working directory): set TRIAGE_DB to a fixed path on persistent disk")
    else:
        add("warn", "database", "in memory: the queue and the decisions are lost when the process stops "
                                "(set TRIAGE_DB, or pass db= to Copilot)")

    n = len(cp.kb.docs)
    if cp.kb.directory.resolve() == kb.KB_DIR.resolve():
        add("warn", "knowledge base", f"the {n} sample articles that ship with the package: set KB_DIR (or pass "
                                      "kb=) to your own folder of help articles (see docs/embedding.md)")
    elif n:
        add("ok", "knowledge base", f"{n} articles in {cp.kb.directory}")
    else:
        add("fail", "knowledge base", f"no .md articles in {cp.kb.directory}: drafts would only say a specialist "
                                      "will reply")

    if llm.enabled():
        add("ok", "model", f"Claude is on ({llm.model_name()})")
        if model:
            out.append(_model_check())
    else:
        add("warn", "model", "no ANTHROPIC_API_KEY: triage uses plain rules and drafts use the template "
                             "(fine to start; see docs/features.md)")

    api_key = cp.api_key or os.getenv("API_KEY")
    connected = [s for s in cp.systems.values() if s.configured]
    if api_key:
        add("ok", "api key", "the review API requires X-API-Key")
    else:
        add("fail" if connected else "warn", "api key",
            "API_KEY is not set: the review API, which returns customer messages, is open"
            + (" and a helpdesk is connected" if connected else " (fine on a laptop, not on a network)"))

    for name, system in cp.systems.items():
        if not system.configured:
            add("ok", name, "no credentials: dry-run. It records what it would send under /outbox and sends nothing")
            continue
        if os.getenv(system.secret_env):
            add("ok", f"{name} webhook secret", f"{system.secret_env} is set: webhooks are signature-checked")
        else:
            add("fail", f"{name} webhook secret", f"{system.secret_env} is not set: anyone who can reach the server "
                                                  "could post tickets, and approvals by tag are refused")
        if live:
            ok, detail = system.check_connection()
            add("ok" if ok else "fail", f"{name} credentials", detail)
        else:
            add("ok", f"{name} credentials", "set (run with --live to test them)")

    tx = cp.taxonomy
    if tx.helpdesk_decisions:
        who = ", ".join(sorted(cp.allowed_reviewers)) if cp.allowed_reviewers else "anyone who can edit the ticket"
        add("ok", "approval by tag", f"agents add {tx.approve} or {tx.reject}; allowed: {who}")
    else:
        add("ok", "approval by tag", "off: decisions are made in the review page or through the API")
    always = f"{tx.tag('triaged')}, {tx.tag('cat_<category>')}, {tx.tag('team_<team>')}"
    sometimes = (f"{tx.tag('lang_<code>')}, {tx.tag('sentiment_negative')}, {tx.tag('spike')} and "
                 f"{tx.tag('possible_duplicate')}")
    add("ok", "tags written", f"{always} on every ticket; {sometimes} when they apply; "
                              f"{tx.tag('rejected')} on a rejection")
    return out


def _model_check() -> Check:
    from .classifier import classify
    t = classify("Duplicate charge on my invoice", "I was charged twice this month, please refund the second payment.")
    if t.source == "llm":
        return Check("ok", "model call", f"answered: {t.category.value} / {t.priority.value} in {llm.model_name()}")
    return Check("fail", "model call", f"fell back to rules: {t.rationale}")


def render(checks: list[Check]) -> str:
    mark = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}
    width = max(len(c.name) for c in checks)
    lines = [f"[{mark[c.status]}] {c.name.ljust(width)}  {c.detail}" for c in checks]
    bad, warn = sum(c.status == "fail" for c in checks), sum(c.status == "warn" for c in checks)
    lines.append("")
    lines.append(f"{bad} to fix, {warn} to look at." if bad or warn else "All good.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true", help="call each connected helpdesk once, read-only")
    ap.add_argument("--model", action="store_true", help="make one small call to Claude")
    args = ap.parse_args(argv)
    path = os.environ.get("TRIAGE_DB") or "triage.db"
    exists = Path(path).is_file()
    try:
        cp = Copilot.from_env(db=":memory:")  # never open the real file for writing just to look at it
    except ValueError as e:  # a setting that cannot work: say which, instead of a traceback
        print(f"[FAIL] configuration  {e}")
        return 1
    checks = run_checks(cp, live=args.live, model=args.model, planned_db=None if exists else path,
                        existing_db=path if exists else None)
    print(render(checks))
    return 1 if any(c.status == "fail" for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
