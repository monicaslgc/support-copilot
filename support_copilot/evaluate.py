"""Run support-copilot over an export of your own tickets, offline, before any helpdesk is connected.

    python -m support_copilot.evaluate tickets.csv                      # a CSV, a JSON list or JSON lines
    python -m support_copilot.evaluate tickets.csv --kb our-articles/   # use your own help articles
    python -m support_copilot.evaluate tickets.csv --out results.csv    # one row per ticket, with the full draft
    python -m support_copilot.evaluate tickets.csv --rules              # rules only, even if a key is set

Each ticket needs `subject` and `body`. `customer` is optional. If a `category` and/or `priority` column holds
what your agents decided (using our names: bug, billing, access, how_to, feature_request, other; P1 to P4), the
summary says how often the triage agrees. Nothing is stored or sent anywhere, and no helpdesk is called: with
ANTHROPIC_API_KEY set, the ticket text (private details masked) goes to Claude: up to two calls per
ticket (one to triage it, one to draft the reply when a help article matches).
"""
import argparse
import csv
import io
import json
import os
import sys
from collections import Counter
from pathlib import Path

from . import llm
from .copilot import Copilot, fit_ticket


def load(path: Path, encoding: str = "utf-8-sig") -> list[dict]:
    try:
        text = path.read_text(encoding=encoding)
    except UnicodeDecodeError:
        raise SystemExit(f"{path}: not readable as {encoding}. Excel often saves CSV as cp1252: "
                         "try --encoding cp1252") from None
    if path.suffix.lower() == ".csv":
        rows = [{k: v for k, v in r.items() if k is not None} for r in csv.DictReader(io.StringIO(text, newline=""))]
    else:
        try:
            rows = json.loads(text)
        except json.JSONDecodeError:  # JSON lines
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise SystemExit(f"{path}: expected a list of tickets (a CSV with a header, a JSON list or JSON lines)")
    missing = [c for c in ("subject", "body") if rows and c not in rows[0]]
    if missing:
        raise SystemExit(f"{path}: every ticket needs a {' and a '.join(missing)} column (found: {', '.join(rows[0])})")
    return rows


def evaluate(rows: list[dict], cp) -> list[dict]:
    results = []
    for i, row in enumerate(rows, 1):
        ticket, cut = fit_ticket(str(row.get("subject") or ""), str(row.get("body") or ""),
                                 str(row.get("customer") or "unknown"))
        s = cp.suggest(ticket)
        results.append({
            "n": i, "subject": ticket.subject, "category": s.triage.category.value, "priority": s.triage.priority.value,
            "team": s.triage.team, "confidence": s.triage.confidence, "source": s.triage.source,
            "sentiment": s.triage.sentiment.value, "language": s.triage.language,
            "article": s.articles[0]["id"] if s.articles else "", "warnings": "; ".join(w.text for w in s.warnings),
            "notices": " ".join(s.notices), "cut": cut, "draft": s.draft_reply,
            "expected_category": str(row.get("category") or "").strip(),
            "expected_priority": str(row.get("priority") or "").strip().upper(),
        })
    return results


def _safe_cell(value):
    """A spreadsheet runs a cell that starts with = + - @ as a formula: prefix those with an apostrophe."""
    return "'" + value if isinstance(value, str) and value[:1] in ("=", "+", "-", "@") else value


def pct(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "n/a"


def summary(results: list[dict], preview: int) -> str:
    n = len(results)
    lines = [f"{n} tickets, triaged by {', '.join(sorted({r['source'] for r in results}))}", ""]
    for title, key in (("category", "category"), ("priority", "priority"), ("language", "language")):
        counts = Counter(r[key] for r in results)
        lines.append(f"by {title}: " + ", ".join(f"{k} {v} ({pct(v, n)})" for k, v in counts.most_common()))
    lines += ["",
              f"a help article was found for {pct(sum(bool(r['article']) for r in results), n)} of the tickets"
              " (the rest get a draft that says a specialist will reply)",
              f"upset customers: {sum(r['sentiment'] == 'negative' for r in results)}; "
              f"drafts with a warning: {sum(bool(r['warnings']) for r in results)}; "
              f"tickets cut to 10,000 characters: {sum(r['cut'] for r in results)}"]
    for what in ("category", "priority"):
        known = [r for r in results if r[f"expected_{what}"]]
        if known:
            agree = sum(r[what].lower() == r[f"expected_{what}"].lower() for r in known)
            lines += ["", f"{what}: agrees with your agents on {pct(agree, len(known))} of {len(known)} tickets"]
            wrong = Counter((r[f"expected_{what}"], r[what]) for r in known
                            if r[what].lower() != r[f"expected_{what}"].lower())
            lines += [f"    your agents said {a}, the triage said {b}: {c}" for (a, b), c in wrong.most_common(5)]
    if preview:
        lines += ["", f"the first {min(preview, n)} drafts:"]
        for r in results[:preview]:
            lines += ["", f"--- #{r['n']} {r['subject']}  ({r['category']}, {r['priority']}, {r['team']})",
                      *("    " + line for line in r["draft"].splitlines())]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tickets", type=Path)
    ap.add_argument("--kb", type=Path, help="a folder of markdown help articles (default: the bundled samples)")
    ap.add_argument("--out", type=Path, help="write one row per ticket to this CSV")
    ap.add_argument("--rules", action="store_true", help="never call Claude, even if a key is set")
    ap.add_argument("--limit", type=int, help="only the first N tickets")
    ap.add_argument("--encoding", default="utf-8-sig", help="the input file's encoding (default utf-8; try cp1252)")
    ap.add_argument("--preview", type=int, default=3, help="how many drafts to print (default 3)")
    args = ap.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be 1 or more")
    if args.out and args.out.resolve() == args.tickets.resolve():
        ap.error("--out would overwrite the tickets file: choose another name")
    if args.rules:
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            os.environ.pop(var, None)
    rows = load(args.tickets, args.encoding)[: args.limit]
    if not rows:
        raise SystemExit(f"{args.tickets}: no tickets")
    if llm.enabled():
        print(f"Claude is on ({llm.model_name()}): up to {2 * len(rows)} model calls. Use --rules to avoid them.\n",
              file=sys.stderr)
    results = evaluate(rows, Copilot(db=":memory:", kb=args.kb))
    print(summary(results, args.preview))
    if args.out:
        with args.out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0]))
            w.writeheader()
            w.writerows({k: _safe_cell(v) for k, v in r.items()} for r in results)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
