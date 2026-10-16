"""Play the user stories (docs/user-stories.md) against the app and check what happens.

    python scripts/demo.py list                  # the scenarios and the stories they cover
    python scripts/demo.py outage                # one scenario, against a server on http://127.0.0.1:8000
    python scripts/demo.py all --url http://host:8000 --api-key SECRET
    python scripts/demo.py all --in-process      # no server: a fresh in-memory app per scenario

Each step prints what is happening and each check prints what was verified; the first check that fails stops
the run with a non-zero exit code. The scenarios use their own customers and ticket ids, so they can be run
several times against the same server. They need no API key and no ticket-system account: the Zammad webhook
runs in dry-run, which records the requests it would send under /outbox.
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

ENV_TO_CLEAR = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ZAMMAD_URL", "ZAMMAD_TOKEN", "ZENDESK_SUBDOMAIN",
                "ZENDESK_EMAIL", "ZENDESK_API_TOKEN", "ZAMMAD_WEBHOOK_SECRET", "ZENDESK_WEBHOOK_SECRET", "API_KEY")


class Failed(AssertionError):
    """A check in a scenario did not hold."""


class Run:
    """The client, a unique id for this run, and the printing."""

    last_uid = 0

    def __init__(self, client, out=print):
        self.c, self.out = client, out
        # unique per run, even when two runs start in the same millisecond
        Run.last_uid = self.uid = max(int(time.time() * 1000) % 10_000_000, Run.last_uid + 1)
        self.steps = 0

    def step(self, text: str) -> None:
        self.steps += 1
        self.out(f"  {self.steps}. {text}")

    def check(self, condition, text: str, show: bool = True) -> None:
        if not condition:
            raise Failed(text)
        if show:
            self.out(f"       ok: {text}")

    def who(self, name: str) -> str:
        return f"{name} (demo {self.uid})"

    def webhook(self, system: str, ticket_id: int, subject: str, body: str, customer: str) -> dict:
        if system == "zammad":
            payload = {"ticket": {"id": ticket_id, "title": subject, "customer": customer},
                       "article": {"body": body, "from": customer}}
        else:
            payload = {"ticket_id": str(ticket_id), "subject": subject, "description": body, "requester": customer}
        r = self.c.post(f"/webhooks/{system}", json=payload)
        self.check(r.status_code == 201, f"the {system} webhook was accepted", show=False)
        return r.json()

    def ticket(self, subject: str, body: str, customer: str) -> dict:
        r = self.c.post("/tickets", json={"subject": subject, "body": body, "customer": customer})
        self.check(r.status_code == 201, f"ticket '{subject}' was triaged", show=False)
        return r.json()

    def get(self, path: str):
        r = self.c.get(path)
        self.check(r.status_code != 401, "the server needs an API key: run again with --api-key", show=False)
        self.check(r.status_code == 200, f"GET {path} answered {r.status_code}", show=False)
        return r.json()

    def review(self, ticket_id: int, **body) -> dict:
        r = self.c.post(f"/tickets/{ticket_id}/review", json={"reviewer": "demo", **body})
        self.check(r.status_code == 200, f"review of ticket {ticket_id} failed: {r.text}", show=False)
        return r.json()

    def written_to(self, system: str, external_id: int) -> list[dict]:
        """What the ticket system received (or, in dry-run, would have): its requests that touch a comment."""
        out = []
        for entry in self.get("/outbox?limit=500"):
            if entry["system"] == system and entry["external_id"] == str(external_id):
                for req in entry["payload"].get("requests", []):
                    if req["path"].endswith("/ticket_articles"):
                        out.append({"kind": entry["kind"], "internal": req["json"]["internal"],
                                    "body": req["json"]["body"]})
        return out


# ------------------------------------------------------------------ scenarios
def triage(r: Run) -> None:
    """US-01, US-02, US-11: a ticket arrives, is triaged and drafted, and waits for a person."""
    health = r.get("/health")
    r.step(f"Start: {'Claude is on' if health['llm'] else 'no API key, so the AI steps use plain rules'}")
    ext = 30_000_000 + r.uid
    r.step("A billing ticket arrives from Zammad")
    t = r.webhook("zammad", ext, "Duplicate charge on invoice",
                  "I was charged twice this month and I would like a refund, please.", r.who("Sam"))
    s = t["suggestion"]["triage"]
    r.check(s["category"] == "billing" and s["priority"] in ("P2", "P3") and s["team"] == "finance",
            f"triaged as {s['category']} / {s['priority']} for {s['team']}")
    r.check(any(a["id"] == "invoices" for a in t["suggestion"]["articles"]), "the invoices help article was found")
    r.check(bool(t["suggestion"]["draft_reply"].strip()), "a draft reply was prepared")

    r.step("What the customer has seen so far")
    notes = r.written_to("zammad", ext)
    r.check(notes and all(n["internal"] for n in notes), "only an internal note was written; nothing is public")

    r.step("The person reviewing sees it in the queue, then edits the reply and approves")
    queue = r.get("/tickets?status=pending_review&limit=500")
    r.check(any(x["id"] == t["id"] for x in queue), "the ticket is in the review queue")
    r.review(t["id"], action="approved", edited_reply="Hi Sam, we found the duplicate charge and are fixing it.")
    public = [n for n in r.written_to("zammad", ext) if not n["internal"]]
    r.check(len(public) == 1 and public[0]["body"] == "Hi Sam, we found the duplicate charge and are fixing it.",
            "the customer receives exactly the text the person approved")

    r.step("A second ticket is rejected instead")
    t2 = r.webhook("zammad", ext + 1, "Duplicate charge on invoice", "Charged twice, refund please", r.who("Ana"))
    r.review(t2["id"], action="rejected", reasons=["duplicate"])
    r.check(all(n["internal"] for n in r.written_to("zammad", ext + 1)), "a rejected ticket sends the customer nothing")


def helpdesk(r: Run) -> None:
    """US-12: an agent approves and rejects by tagging the ticket in the helpdesk (Zammad payloads, dry-run)."""
    ext = 40_000_000 + r.uid
    r.step("A billing ticket arrives from Zammad and gets an internal note")
    t = r.webhook("zammad", ext, "Duplicate charge on invoice", "I was charged twice, refund please.", r.who("Sam"))
    r.check(t["status"] == "pending_review", "it waits for a person; the customer has seen nothing")
    notes = r.written_to("zammad", ext)
    r.check(any("add the tag ai_approve" in n["body"] for n in notes), "the note says which tag sends the reply")

    def tagged(tid: int, title: str, body: str, *tags: str):
        payload = {"ticket": {"id": tid, "title": title, "customer": "Sam", "tags": list(tags),
                              "updated_by": {"email": "ana@example.com"}}, "article": {"body": body, "from": "Sam"}}
        return r.c.post("/webhooks/zammad", json=payload).json()

    r.step("The agent adds the tag ai_approve in Zammad; Zammad calls the webhook")
    done = tagged(ext, "Duplicate charge on invoice", "I was charged twice, refund please.", "ai_triaged", "ai_approve")
    public = [n for n in r.written_to("zammad", ext) if not n["internal"]]
    r.check(done.get("decided_in") == "zammad" and len(public) == 1, "the drafted reply was sent to the customer")
    r.check(r.get(f"/tickets/{t['id']}")["review"]["reviewer"] == "ana@example.com", "and the decision is hers")

    r.step("Zammad sends the same update again")
    tagged(ext, "Duplicate charge on invoice", "I was charged twice, refund please.", "ai_triaged", "ai_approve")
    r.check(len([n for n in r.written_to("zammad", ext) if not n["internal"]]) == 1, "nothing more is sent")

    r.step("A second ticket is tagged ai_reject")
    r.webhook("zammad", ext + 1, "Duplicate charge on invoice", "Charged twice, refund please", r.who("Rui"))
    tagged(ext + 1, "Duplicate charge on invoice", "Charged twice, refund please", "ai_reject")
    r.check(all(n["internal"] for n in r.written_to("zammad", ext + 1)), "a rejected ticket sends the customer nothing")


def outage(r: Run) -> None:
    """US-03: several customers report the same outage; one click answers them all."""
    reports = [
        ("Platform is down", "Nobody on my team can access anything, total outage since 9am."),
        ("Cannot reach the platform", "The platform is unavailable for our whole team, is it down?"),
        ("Service unavailable", "The platform is slow and unavailable, is there an outage?"),
        ("Everything is down", "Our users all report the platform is down. Outage?"),
    ]
    base = 31_000_000 + r.uid
    r.step("Four different customers report the same problem within minutes")
    mine = [r.webhook("zammad", base + i, s, b, r.who(n))["id"]
            for i, ((s, b), n) in enumerate(zip(reports, ("Ana", "Rui", "Sam", "Joana"), strict=True))]
    r.step("The queue groups them as one possible incident")
    cluster = next((c for c in r.get("/clusters") if set(mine) <= set(c["ticket_ids"])), None)
    r.check(cluster is not None, "the four tickets share a group")
    r.check(cluster["kind"] == "spike" and cluster["customers"] >= 4,
            f"it is a possible incident: {cluster['size']} tickets from {cluster['customers']} customers")
    r.check(cluster["topic_title"] == "Service outage or platform unavailable", "about the outage help article")

    r.step("One decision answers every pending ticket in it")
    res = r.c.post(f"/clusters/{cluster['id']}/review", json={
        "action": "approved", "reviewer": "demo", "edited_reply": "We know about the outage and are working on it.",
        "ticket_ids": cluster["pending_ids"]})
    r.check(res.status_code == 200 and set(mine) <= set(res.json()["reviewed"]), "all four were reviewed together")
    for i in range(4):
        replies = [n for n in r.written_to("zammad", base + i) if not n["internal"]]
        r.check(len(replies) == 1 and "outage" in replies[0]["body"], f"customer {i + 1} got the reply")
    r.check(all(r.get(f"/tickets/{t}")["review"]["reviewer"] == "demo" for t in mine),
            "each ticket kept its own review")

    r.step("One customer sending the same thing twice is a duplicate, not an incident")
    who = r.who("Leo")
    words = " ".join(f"zq{r.uid}{i}" for i in range(6))      # words no other ticket uses, so only these two can link
    a = r.ticket(f"Widget {words}", f"My widget {words} refuses to sync", who)
    b = r.ticket(f"Widget {words}", f"My widget {words} refuses to sync", who)         # the same message, sent again
    tb = r.get(f"/tickets/{b['id']}")
    r.check(tb["duplicate_of"] == a["id"], "the second ticket is flagged as a possible duplicate of the first")
    r.check(tb["cluster"]["customers"] == 1 and tb["cluster"]["kind"] == "related",
            "the two tickets are linked, but one customer is not an incident")
    refused = r.c.post(f"/clusters/{tb['cluster']['id']}/review", json={"action": "approved", "reviewer": "demo",
                                                                           "ticket_ids": [a["id"], b["id"]]})
    r.check(refused.status_code == 409, "so a bulk answer is refused: it is only for incidents")


def upset(r: Run) -> None:
    """US-04, US-06: an upset Portuguese customer, with history."""
    rui = r.who("Rui Costa")
    r.step("Rui writes in Portuguese about an export problem")
    first = r.ticket("Exportação falha", "Não consigo exportar o relatório mensal para CSV.", rui)
    r.check(first["suggestion"]["triage"]["language"] == "pt", "the language was recognised: Portuguese")
    r.step("He writes again, upset")
    t = r.ticket("Isto é inaceitável", "A exportação continua a falhar, é a terceira vez! Péssimo serviço, "
                 "quero falar com um responsável.", rui)
    tri, draft = t["suggestion"]["triage"], t["suggestion"]["draft_reply"]
    r.check(tri["sentiment"] == "negative", "he sounds upset")
    r.check("Raised from P3" in tri["rationale"] and tri["priority"] == "P2",
            "so the ticket moved up from P3 to P2, and says why")
    r.check(draft.startswith("Olá") and "Lamentamos" in draft, "the draft is in Portuguese and starts with an apology")
    r.step("The reviewer sees who he is")
    hist = r.get(f"/tickets/{t['id']}")["history"]
    r.check(hist["count"] >= 1 and any(h["id"] == first["id"] for h in hist["recent"]), "his earlier ticket is listed")
    r.step("An outage stays P1 however angry the customer is, and an unknown customer has no history")
    out = r.ticket("A plataforma está em baixo", "Total outage, inaceitável!! Ninguém consegue aceder.", "unknown")
    r.check(out["suggestion"]["triage"]["priority"] == "P1", "P1 does not move")
    r.check(r.get(f"/tickets/{out['id']}")["history"]["count"] == 0, "no history for 'unknown'")


def promise(r: Run) -> None:
    """US-05: replies that promise something are flagged; the person still decides."""
    r.step("A reply that promises a refund")
    res = r.c.post("/check-reply", json={"text": "Hi, we will refund you in full today."}).json()
    kinds = {(w["kind"], w["severity"]) for w in res["warnings"]}
    r.check(("refund", "hard") in kinds, f"flagged as a hard promise: this reply {res['explanation']}")
    r.step("A calm reply, and one with an amount and a deadline")
    r.check(r.c.post("/check-reply", json={"text": "A specialist will look into this."}).json()["warnings"] == [],
            "a plain reply has no warnings")
    soft = r.c.post("/check-reply", json={"text": "It costs €49 and we reply within 2 days."}).json()["warnings"]
    r.check({w["kind"] for w in soft} == {"money", "deadline"} and all(w["severity"] == "soft" for w in soft),
            "an amount and a deadline are flagged softly")
    r.step("The review page asks for a second click; the API lets a person send it and says what it found")
    t = r.ticket("Duplicate charge", "refund my invoice please", r.who("Mia"))
    done = r.review(t["id"], action="approved", edited_reply="Hi, we have refunded the duplicate charge.")
    r.check([w["kind"] for w in done["warnings"]] == ["refund"], "the decision went through, with the warning returned")


def privacy(r: Run) -> None:
    """US-06: private details are masked before text goes to a model; the ticket keeps the original."""
    from support_copilot.guardrails import redact

    text = ("Charged twice. Write to ana.silva@example.com or call +351 912 345 678. "
            "Card 4111 1111 1111 1111, order 1234 5678 9012 3456.")
    r.step("What a model would receive instead of this ticket text")
    masked, counts = redact(text)
    r.out(f"       before: {text}\n       after:  {masked}")
    r.check(counts == {"email": 1, "card": 1, "phone": 1}, f"masked {counts}")
    r.check("1234 5678 9012 3456" in masked, "the order number is left alone (it is not a valid card number)")
    r.step("The ticket itself keeps the original, because the reviewer needs it")
    t = r.ticket("Duplicate charge", text, r.who("Ana"))
    r.check("ana.silva@example.com" in r.get(f"/tickets/{t['id']}")["body"], "the stored ticket is unchanged")
    r.out("       (with an API key set, the review page also says: 'Masked before sending to the AI: 1 email, ...')")


def feedback(r: Run) -> None:
    """US-07, US-08: why people correct the AI, and where it has earned trust."""
    r.step("Eight billing tickets are reviewed: five untouched, three corrected")
    text = ("Duplicate charge", "I was charged twice, I want a refund for this invoice")
    ids = [r.ticket(*text, "unknown")["id"] for _ in range(8)]
    for t in ids[:5]:
        r.review(t, action="approved")
    r.review(ids[5], action="approved", final_priority="P2", reasons=["wrong_priority"])
    r.review(ids[6], action="approved", edited_reply="Hi, this is sorted.", reasons=["tone"], note="too formal")
    r.review(ids[7], action="rejected", reasons=["duplicate"])
    r.step("What the numbers say")
    m = r.get("/metrics/breakdown")
    row = next(c for c in m["by_category"] if c["category"] == "billing")
    r.out(f"       billing: {row['reviewed']} reviewed, clean {row['clean_rate']:.0%}, lower bound "
          f"{row['clean_rate_lower_bound']:.0%}, rejected {row['rejection_rate']:.0%}")
    r.check(row["reviewed"] >= 8 and row["clean_rate"] < 1, "the category shows corrections, not a perfect record")
    r.check(row["clean_rate_lower_bound"] < row["clean_rate"], "the confidence bound is lower than the raw rate")
    r.check(not row["automation_candidate"], "it is not an automation candidate")
    r.check({"wrong_priority", "tone", "duplicate"} <= set(m["reasons"]), f"reasons recorded: {m['reasons']}")
    r.check("nothing is automated" in m["automation"]["note"].lower(), "and nothing is ever automated from this")


SCENARIOS = {
    "triage": (triage, "US-01, US-02, US-11"),
    "helpdesk": (helpdesk, "US-12"),
    "outage": (outage, "US-03"),
    "upset": (upset, "US-04, US-06"),
    "promise": (promise, "US-05"),
    "privacy": (privacy, "US-06"),
    "feedback": (feedback, "US-07, US-08"),
}


# ------------------------------------------------------------------ running
def in_process_client():
    """A fresh app with an in-memory database and no credentials, so a scenario starts from nothing."""
    for var in ENV_TO_CLEAR:
        os.environ.pop(var, None)
    os.environ["TRIAGE_DB"] = ":memory:"
    from fastapi.testclient import TestClient

    from support_copilot import main
    from support_copilot.db import connect
    from support_copilot.ticketsystems import build_systems

    main.copilot.conn, main.copilot.systems = connect(":memory:"), build_systems()
    return TestClient(main.app)


def run(argv: list[str] | None = None, out=print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenario", choices=["list", "all", *SCENARIOS])
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--api-key")
    ap.add_argument("--in-process", action="store_true", help="no server: a fresh in-memory app per scenario")
    args = ap.parse_args(argv)

    if args.scenario == "list":
        for name, (fn, stories) in SCENARIOS.items():
            out(f"{name:9} {stories:20} {fn.__doc__.split(':', 1)[1].strip()}")
        return 0

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for name in names:
        fn, stories = SCENARIOS[name]
        out(f"\n== {name} ({stories}): {fn.__doc__.split(':', 1)[1].strip()}")
        if args.in_process:
            client = in_process_client()
        else:
            headers = {"X-API-Key": args.api_key} if args.api_key else {}
            client = httpx.Client(base_url=args.url, headers=headers, timeout=30)
        try:
            fn(Run(client, out))
        except Failed as e:
            out(f"\nFAILED in '{name}': {e}")
            return 1
        except httpx.TransportError as e:
            out(f"\nCould not reach {args.url}: is the server running? ({e})")
            return 2
    out(f"\nAll {len(names)} scenario{'s' if len(names) != 1 else ''} passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
