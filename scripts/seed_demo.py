"""Load the sample tickets into a running server, as if a ticket system had sent them.

    uvicorn support_copilot.main:app &
    python scripts/seed_demo.py                 # http://127.0.0.1:8000
    python scripts/seed_demo.py http://host:8000 --api-key SECRET
"""
import argparse
import json
from pathlib import Path

import httpx

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "sample_tickets.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url", nargs="?", default="http://127.0.0.1:8000")
    ap.add_argument("--api-key")
    args = ap.parse_args()
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=30) as c:
        for t in json.loads(SAMPLES.read_text()):
            r = c.post("/tickets", json=t)
            r.raise_for_status()
            tr = r.json()["suggestion"]["triage"]
            print(f"#{r.json()['id']} {tr['priority']} {tr['category']:<16} {t['subject']}")


if __name__ == "__main__":
    main()
