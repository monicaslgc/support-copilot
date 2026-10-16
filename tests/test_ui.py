"""The review page: brand, readability and the properties that keep the human decision unmistakable.

The page is one static file, so these are checks on its source plus the route that serves it. The behaviour
(approve, reject, bulk, hostile ticket text shown as text) is driven in a real browser by hand, see docs/engineering.md.
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

PAGE = (Path(__file__).resolve().parent.parent / "app" / "static" / "index.html").read_text(encoding="utf-8")


def tokens(block: str) -> dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r"--([\w-]+):\s*([^;]+);", block)}


def light_dark() -> tuple[dict[str, str], dict[str, str]]:
    root = re.search(r":root \{(.*?)\n\}", PAGE, re.S).group(1)
    dark_rule = r'prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{(.*?)\n  \}'
    dark = re.search(dark_rule, PAGE, re.S).group(1)
    light = tokens(root)
    return resolve(light), resolve({**light, **tokens(dark)})


def resolve(scheme: dict[str, str]) -> dict[str, str]:
    """Follow var(--x) references until every token is a plain colour (fonts are left alone)."""
    out = dict(scheme)
    for _ in range(3):
        out = {k: (out.get(v[6:-1], v) if v.startswith("var(--") else v) for k, v in out.items()}
    return out


def luminance(hex_colour: str) -> float:
    rgb = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_page_is_served():
    r = TestClient(app).get("/")
    assert r.status_code == 200 and "Review queue" in r.text


def test_it_uses_the_brand_palette_and_typefaces():
    light, _ = light_dark()
    brand = (light["cream"], light["sage"], light["forest"], light["charcoal"])
    assert brand == ("#F4F1E8", "#A0B3A0", "#153D32", "#202A27")
    assert (light["bg"], light["ink"], light["accent"]) == (light["cream"], light["charcoal"], light["forest"])
    for face in ("Inter", "JetBrains Mono", "DM Serif Display"):
        assert face in PAGE


def test_text_keeps_aa_contrast_in_light_and_dark():
    for scheme in light_dark():
        pairs = [("ink", "bg"), ("ink", "panel"), ("muted", "bg"), ("muted", "panel"), ("muted", "ai-bg"),
                 ("ink", "ai-bg"), ("bad", "panel"), ("bad", "bad-bg"), ("warn", "warn-bg"), ("p1", "panel"),
                 ("p2", "panel"), ("p3", "panel"), ("p4", "panel"), ("accent-ink", "accent"), ("accent", "panel")]
        for fg, bg in pairs:
            ratio = contrast(scheme[fg], scheme[bg])
            assert ratio >= 4.5, f"--{fg} on --{bg} is {ratio:.2f}:1"


def test_ticket_text_is_never_inserted_as_html_and_nothing_is_loaded_from_outside():
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", PAGE)
    assert not re.search(r"(src|href)=\"https?:|@import|url\(\s*[\"']?https?:", PAGE)


def test_ticket_ai_suggestion_and_human_decision_are_visually_separate():
    assert 'class: "sec ticket"' in PAGE and 'class: "sec ai"' in PAGE and 'class: "sec human"' in PAGE
    assert '"AI SUGGESTION"' in PAGE and '"AI DRAFT"' in PAGE and '"HUMAN"' in PAGE and "Your decision" in PAGE


def test_the_approval_step_stays_explicit():
    for needle in ('id: "approve"', 'id: "reject"', "Approve and send", "Send anyway", "Enter your name first.",
                   "Nothing reaches the customer until you approve."):
        assert needle in PAGE
    # a promise needs a second click, and the warning is announced to screen readers
    assert 'flags.some((f) => f.severity === "hard") && !armed' in PAGE
    assert '"banner hard"' in PAGE and 'role: "status"' in PAGE


def test_decisions_are_shown_as_text_not_only_as_colour():
    assert "Approved by a person" in PAGE and "Rejected by a person" in PAGE
    assert ".status.approved" in PAGE and ".status.rejected" in PAGE


def test_empty_loading_and_error_states_exist():
    for text in ("Loading tickets…", "The queue is clear. Nothing is waiting for review.", "Could not load tickets.",
                 "No pending tickets in this incident."):
        assert text in PAGE
    assert 'role: "alert"' in PAGE


def test_keyboard_and_responsive_basics():
    assert ":focus-visible" in PAGE and 'class="skip"' in PAGE and 'lang="en"' in PAGE
    assert "prefers-color-scheme: dark" in PAGE and "max-width: 760px" in PAGE and 'name="viewport"' in PAGE
    assert "min-height: 40px" in PAGE   # touch targets for the decision buttons
