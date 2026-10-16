"""Regenerate the README screenshots from the running app, with synthetic demo data only.

    pip install playwright            # not a project dependency: only needed here
    python scripts/capture_screenshots.py                       # writes docs/*.png
    python scripts/capture_screenshots.py --fonts DIR           # optional, see below

Each scene starts its own app on a throwaway SQLite file, feeds it invented tickets through the real API
(the same calls the webhooks use) and drives the real review page in Chromium. Nothing here touches a ticket
system or a live model. The guardrails scene replaces the model with a stub that returns canned JSON, as the
README says; every other scene runs in rules mode.

CHROMIUM_PATH can name a Chromium binary when Playwright's own download is not installed.

--fonts DIR points at a folder holding JetBrains Mono and a serif (as @fontsource woff2 files). The page falls
back to system fonts without it, so a screenshot taken on a machine without the brand fonts looks plainer.
"""
import argparse
import base64
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CLEAR = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ZAMMAD_URL", "ZAMMAD_TOKEN", "ZENDESK_SUBDOMAIN",
         "ZENDESK_EMAIL", "ZENDESK_API_TOKEN", "ZAMMAD_WEBHOOK_SECRET", "ZENDESK_WEBHOOK_SECRET", "API_KEY")

STUB = """
import json, uvicorn
from app import llm
def complete(system, user, max_tokens=2048):
    if '"category"' in system:
        return json.dumps({"category": "billing", "priority": "P3", "confidence": 0.9,
                           "rationale": "The customer reports a duplicate charge.",
                           "sentiment": "neutral", "language": "en"})
    return "Hi Ana, thanks for reaching out. We are looking into the double charge."
llm.complete = complete
llm.enabled = lambda: True
uvicorn.run("app.main:app", host="127.0.0.1", port=%d, log_level="warning")
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def server(stub: bool = False):
    port = free_port()
    env = {k: v for k, v in os.environ.items() if k not in CLEAR}
    with tempfile.TemporaryDirectory() as tmp:
        env["TRIAGE_DB"] = str(Path(tmp) / "demo.db")
        cmd = ([sys.executable, "-c", STUB % port] if stub
               else [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"])
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env)
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                with contextlib.suppress(httpx.HTTPError):
                    if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                        break
                time.sleep(0.1)
            else:
                raise RuntimeError("the app did not start")
            yield url
        finally:
            proc.terminate()
            proc.wait(10)


def post(url: str, subject: str, body: str, customer: str) -> int:
    r = httpx.post(f"{url}/tickets", json={"subject": subject, "body": body, "customer": customer}, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def review(url: str, tid: int, **extra) -> None:
    httpx.post(f"{url}/tickets/{tid}/review", json={"action": "approved", "reviewer": "Demo reviewer", **extra},
               timeout=30).raise_for_status()


def font_css(fonts: Path | None) -> str:
    """@font-face rules for the optional brand fonts. A serif is registered as Georgia, the face the page asks for."""
    if not fonts:
        return ""
    css = []
    for family, pattern in (("JetBrains Mono", "jetbrains-mono-latin-{w}-normal.woff2"),
                            ("Georgia", "newsreader-latin-{w}-normal.woff2")):
        for w in (400, 500, 600):
            f = next(fonts.rglob(pattern.format(w=w)), None)
            if f:
                data = base64.b64encode(f.read_bytes()).decode()
                css.append(f'@font-face{{font-family:"{family}";font-weight:{w};src:url(data:font/woff2;base64,{data})}}')
    return "".join(css)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonts", type=Path, help="folder with @fontsource woff2 files (optional)")
    ap.add_argument("--out", type=Path, default=DOCS)
    args = ap.parse_args()
    fonts = font_css(args.fonts)
    out = args.out

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)

        def page(url, width=1180, height=860, dark=False, scale=2):
            ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale,
                                      color_scheme="dark" if dark else "light")
            p = ctx.new_page()
            p.goto(url)
            if fonts:
                p.add_style_tag(content=fonts)
            p.wait_for_selector("#list .item, #list .empty")
            return p

        def shot(p, name, full=True):
            p.evaluate("document.fonts.ready")
            p.wait_for_timeout(250)
            p.screenshot(path=str(out / name), full_page=full)
            print("wrote", out / name)

        def pick(p, subject):
            p.locator("#list .item", has_text=subject).first.click()
            p.wait_for_selector("#approve, .status")

        # 1. The sample queue, and what a decision looks like afterwards.
        with server() as url:
            for t in json.loads((ROOT / "data" / "sample_tickets.json").read_text()):
                post(url, t["subject"], t["body"], t["customer"])
            p = page(url)
            pick(p, "Duplicate charge on invoice")
            p.wait_for_timeout(500)
            shot(p, "review-queue.png")
            p.fill("#reply", "Hi Sam,\n\nSorry about the double charge. I have passed it to our finance team, "
                             "who will check the invoice and write to you once they have.\n\n"
                             "Best regards,\nSupport Team")
            p.select_option("#prio", "P2")
            p.fill("#reviewer", "Monica")
            p.click("#approve")
            p.wait_for_selector("#flash.ok")
            shot(p, "after-approval.png")
            p.select_option("#filter", "approved")
            p.wait_for_selector("#list .item")
            p.locator("#list .item").first.click()
            p.wait_for_selector(".status")
            shot(p, "approved-view.png")
            m = page(url, width=390, height=844, dark=True)
            pick(m, "Platform is down")
            m.locator(".sec.human").evaluate("e => e.scrollIntoView({block: 'end'})")
            shot(m, "mobile-dark.png", full=False)

        # 2. An outage: four customers, one incident.
        with server() as url:
            for subject, customer in (("Platform is down", "Ana"), ("Cannot reach the platform", "Rui"),
                                      ("Service unavailable", "Sam"), ("Everything is down", "Joana")):
                post(url, subject, "Nobody on my team can access anything, total outage since 9am.", customer)
            p = page(url, height=900)
            p.wait_for_selector("#bulk")
            shot(p, "incident-spike.png")

        # 3. Customer context: an upset Portuguese-speaking customer with earlier tickets.
        with server() as url:
            post(url, "Exportação falha", "A exportação do relatório não funciona.", "Rui Costa")
            post(url, "Fatura em falta", "Não encontro a fatura do mês passado.", "Rui Costa")
            post(url, "Isto é inaceitável", "A exportação continua a falhar, é a terceira vez! Péssimo serviço, "
                 "quero falar com um responsável.", "Rui Costa")
            post(url, "How to export a report?", "How do I export the monthly report to CSV?", "Joana")
            p = page(url, height=980)
            pick(p, "Isto é inaceitável")
            shot(p, "customer-context.png")

        # 4. Guardrails, with a stub model so the notices show without an API key.
        with server(stub=True) as url:
            post(url, "Duplicate charge on invoice", "I was charged twice. Write to ana.silva@example.com or call "
                 "+351 912 345 678. Card 4111 1111 1111 1111.", "Ana")
            p = page(url, height=1020)
            # A model-written promise is thrown away server-side, so the warning is shown on a reviewer's edit.
            p.fill("#reply", "Hi Ana, we will refund you in full today and it is free of charge.")
            p.wait_for_selector(".banner.hard")
            shot(p, "guardrails.png")

        # 5. Insights: synthetic reviews. The headline agreement is high while one category is weaker.
        with server() as url:
            plan = {
                "Error when saving contact": ("bug", 5, 2),
                "Duplicate charge on invoice": ("billing", 6, 0),
                "How to export a report?": ("how_to", 6, 0),
                "I cannot log in": ("access", 4, 0),
            }
            bodies = {"bug": "I get an error and the page crashes when saving a contact.",
                      "billing": "I was charged twice this month, I want a refund.",
                      "how_to": "How do I export the monthly report to CSV?",
                      "access": "I forgot my password and the reset link does not arrive."}
            n = 0
            for subject, (cat, count, edited) in plan.items():
                for i in range(count):
                    n += 1
                    tid = post(url, subject, bodies[cat], f"Customer {n}")
                    if i < edited:
                        review(url, tid, edited_reply="Hi, thanks for flagging this. We are looking into it.",
                               reasons=["tone"])
                    else:
                        review(url, tid)
            p = page(url, height=700)
            p.click("#tab")
            p.wait_for_selector("#insights table")
            shot(p, "insights.png")

        browser.close()


if __name__ == "__main__":
    main()
