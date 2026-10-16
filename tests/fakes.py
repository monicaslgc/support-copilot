"""Stateful fakes of the Zendesk and Zammad APIs.

They apply each system's real update semantics (additive tags, public vs internal),
and expose the same read API so one scenario can be asserted against both systems.
"""
import base64
import json

import httpx

from support_copilot.ticketsystems import ZammadSystem, ZendeskSystem


class FakeSystem:
    def __init__(self):
        self.tickets, self.requests = {}, []

    def seed(self, tid, tags=()):
        self.tickets[int(tid)] = {"tags": set(tags), "priority": None, "type": None,
                                  "status": "new", "articles": []}

    def transport(self):
        return httpx.MockTransport(self.handler)

    def public(self, tid):
        return [a["body"] for a in self.tickets[int(tid)]["articles"] if a["public"]]

    def internal(self, tid):
        return [a["body"] for a in self.tickets[int(tid)]["articles"] if not a["public"]]

    def tags(self, tid):
        return self.tickets[int(tid)]["tags"]

    def priority(self, tid):
        return self.tickets[int(tid)]["priority"]


class FakeZendesk(FakeSystem):
    name = "zendesk"
    PRIO = {"P1": "urgent", "P2": "high", "P3": "normal", "P4": "low"}

    def make(self):
        return ZendeskSystem("acme", "a@acme.com", "tok", transport=self.transport())

    def payload(self, tid, subject, desc, who):
        return {"ticket_id": str(tid), "subject": subject, "description": desc, "requester": who}

    def handler(self, req: httpx.Request):
        self.requests.append(req)
        expected = base64.b64encode(b"a@acme.com/token:tok").decode()
        if req.headers.get("authorization") != f"Basic {expected}":
            return httpx.Response(401)
        tid = int(req.url.path.split("/")[-1].removesuffix(".json"))
        if tid not in self.tickets:
            return httpx.Response(404)
        t, body = self.tickets[tid], json.loads(req.content)["ticket"]
        t["articles"].append(body["comment"])
        for f in ("priority", "type", "status"):
            if f in body:
                t[f] = body[f]
        t["tags"] |= set(body.get("additional_tags", []))   # additional_tags = union
        if "tags" in body:
            t["tags"] = set(body["tags"])                    # tags = REPLACE
        return httpx.Response(200, json={"ticket": {}})


class FakeZammad(FakeSystem):
    name = "zammad"
    PRIO = {"P1": "high", "P2": "high", "P3": "normal", "P4": "low"}
    _NAMES = {3: "high", 2: "normal", 1: "low"}

    def make(self):
        return ZammadSystem("https://help.example.com", "tok", transport=self.transport())

    def payload(self, tid, subject, desc, who):
        return {"ticket": {"id": int(tid), "title": subject, "customer": who},
                "article": {"body": desc, "from": who}}

    def priority(self, tid):
        pid = self.tickets[int(tid)]["priority"]
        return self._NAMES.get(pid)

    def handler(self, req: httpx.Request):
        self.requests.append(req)
        if req.headers.get("authorization") != "Token token=tok":
            return httpx.Response(401)
        body, path = json.loads(req.content), req.url.path
        if path == "/api/v1/ticket_articles":
            tid = body["ticket_id"]
            if tid not in self.tickets:
                return httpx.Response(422, json={"error": "Couldn't find Ticket"})
            self.tickets[tid]["articles"].append(
                {"body": body["body"], "public": not body["internal"], "type": body["type"]})
        elif path.startswith("/api/v1/tickets/") and req.method == "PUT":
            tid = int(path.rsplit("/", 1)[1])
            if tid not in self.tickets:
                return httpx.Response(404)
            self.tickets[tid]["priority"] = body["priority_id"]
        elif path == "/api/v1/tags/add":
            if body["o_id"] not in self.tickets:
                return httpx.Response(404)
            self.tickets[body["o_id"]]["tags"].add(body["item"])    # additive
        else:
            return httpx.Response(404)
        return httpx.Response(200, json={})
