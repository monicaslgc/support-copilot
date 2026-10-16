"""Render the HTML sources in docs/src/ and marketing/ to PNG with Chromium (Playwright).

    pip install playwright
    python scripts/render_assets.py [--fonts DIR]

docs/src/architecture.py        -> docs/src/architecture.svg -> docs/architecture.png   (the author's diagram kit style)
docs/src/social-preview.html    -> docs/social-preview.png   (1280 x 640, the GitHub social preview)
marketing/linkedin/src/post-graphic.html -> marketing/linkedin/images/support-copilot-4x5.png   (1080 x 1350)

The sources use the brand tokens in docs/src/brand.css. Fonts: see scripts/capture_screenshots.py (--fonts).
Screenshots are not generated here: run scripts/capture_screenshots.py first, the sources embed docs/*.png.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_screenshots import ROOT, font_css  # noqa: E402

subprocess.run([sys.executable, str(ROOT / "docs/src/architecture.py")], check=True)

JOBS = [
    ("docs/src/architecture.svg", "docs/architecture.png", (1600, 900), 2),
    ("docs/src/social-preview.html", "docs/social-preview.png", (1280, 640), 1),
    ("marketing/linkedin/src/post-graphic.html", "marketing/linkedin/images/support-copilot-4x5.png", (1080, 1350), 1),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonts", type=Path)
    fonts = font_css(ap.parse_args().fonts)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)
        for src, dst, size, scale in JOBS:
            width, height = size or (1100, 600)
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=scale)
            if src.endswith(".svg"):
                svg = (ROOT / src).read_text(encoding="utf8")
                page.set_content(f'<body style="margin:0;background:#F4F1E8">{svg}</body>')
            else:
                page.goto((ROOT / src).as_uri())
            if fonts:
                page.add_style_tag(content=fonts)
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(250)
            target = ROOT / dst
            target.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(target), full_page=size is None)
            print("wrote", target)
        browser.close()


if __name__ == "__main__":
    main()
