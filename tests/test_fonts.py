"""The fonts ship with the review page, so it looks the same everywhere and asks nothing of a third party."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from support_copilot import main

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "support_copilot" / "static" / "index.html").read_text(encoding="utf-8")


def test_every_font_the_page_asks_for_is_served():
    urls = re.findall(r'url\("(/static/fonts/[^"]+\.woff2)"\)', PAGE)
    assert len(urls) == 6
    c = TestClient(main.app)
    for url in urls:
        r = c.get(url)
        assert r.status_code == 200 and len(r.content) > 10_000 and r.content[:4] == b"wOF2", url


def test_the_fonts_are_declared_for_the_families_the_page_uses():
    families = set(re.findall(r'font-family: "([^"]+)"; font-weight', PAGE))
    assert families == {"DM Serif Display", "Inter", "JetBrains Mono"}


def test_the_fonts_are_shipped_in_the_package():
    assert "static/fonts/*" in (ROOT / "pyproject.toml").read_text()
    assert (ROOT / "support_copilot" / "static" / "fonts" / "README.md").is_file()
