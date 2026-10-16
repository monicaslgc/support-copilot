"""The documentation does not rot: links, images, anchors and the numbers it quotes."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC_FILES = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def headings(path: Path) -> set[str]:
    """GitHub-style anchors of a markdown file (fenced code is skipped, so shell comments are not headings)."""
    out, fenced = set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and (m := re.match(r"#{1,6}\s+(.*)", line)):
            out.add(re.sub(r"[^\w\- ]", "", m.group(1).strip().lower()).replace(" ", "-"))
    return out


def links(path: Path):
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced:
            yield from LINK.findall(line)


@pytest.mark.parametrize("doc", DOC_FILES, ids=lambda p: p.name)
def test_every_relative_link_and_anchor_resolves(doc):
    for target in links(doc):
        if re.match(r"(https?:|mailto:)", target):
            continue
        file_part, _, anchor = target.partition("#")
        dest = (doc.parent / file_part).resolve() if file_part else doc
        assert dest.exists(), f"{doc.name}: {target} points to a missing file"
        if anchor:
            assert dest.suffix == ".md", f"{doc.name}: {target} has an anchor on a non-markdown file"
            assert anchor in headings(dest), f"{doc.name}: {target} has no matching heading in {dest.name}"


def test_no_screenshot_is_orphaned():
    used = {(doc.parent / t.partition("#")[0]).resolve() for doc in DOC_FILES for t in links(doc)
            if not re.match(r"(https?:|mailto:)", t)}
    orphans = [p.name for p in sorted((ROOT / "docs").glob("*.png")) if p.resolve() not in used]
    assert orphans == [], f"images no document uses: {orphans}"


def test_the_readme_is_a_front_page_not_a_manual():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert len(readme.splitlines()) < 120
    assert readme.splitlines()[0] == "# support-copilot"
    assert "docs/limitations.md" in readme and "Python 3.11" in readme
    assert "has not run against" in readme.lower() or "not verified" in readme.lower()   # honesty stays up front


def test_the_test_count_quoted_in_the_docs_is_the_real_one():
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    count = int(re.search(r"(\d+) tests? collected", out).group(1))
    for doc in (ROOT / "README.md", ROOT / "docs" / "engineering.md"):
        quoted = {int(n) for n in re.findall(r"\b(\d+) tests\b", doc.read_text(encoding="utf-8"))}
        assert quoted == {count}, f"{doc.name} says {sorted(quoted)} tests, there are {count}"
