"""The documentation does not rot: links, images, anchors and the numbers it quotes."""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC_FILES = [ROOT / name for name in ("README.md", "CONTRIBUTING.md", "SECURITY.md", "examples/README.md")]
DOC_FILES += sorted((ROOT / "docs").glob("*.md"))
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
    assert len(readme.splitlines()) < 140
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


def _stories() -> dict[str, str]:
    """US-xx -> the text of its section in docs/user-stories.md."""
    doc = (ROOT / "docs" / "user-stories.md").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^(?=### US-)", doc)[1:]
    return {re.match(r"### (US-\d+)", p).group(1): p for p in parts}


def _demo_scenarios() -> dict:
    spec = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    return demo.SCENARIOS


def test_every_user_story_has_a_test_and_every_story_test_has_a_story():
    stories = _stories()
    assert len(stories) == 12
    code = (ROOT / "tests" / "test_user_stories.py").read_text(encoding="utf-8")
    defined = set(re.findall(r"(?m)^def (test_us\d+\w*)\(", code))
    referenced = set(re.findall(r"test_user_stories\.py::(test_us\d+\w*)", "\n".join(stories.values())))
    assert referenced == defined, f"dangling: {referenced - defined}, undocumented: {defined - referenced}"
    for story_id, text in stories.items():
        number = story_id.split("-")[1]
        assert re.search(rf"::test_us{number}\w*", text), f"{story_id} names no test"
        assert "**As**" in text and "Acceptance criteria:" in text, f"{story_id} is missing its story or criteria"


def test_the_scenarios_the_stories_point_to_exist_and_cover_them():
    scenarios = _demo_scenarios()
    for story_id, text in _stories().items():
        for name in re.findall(r"python scripts/demo\.py (\w+)", text):
            assert name in scenarios, f"{story_id} points to a scenario that does not exist: {name}"
            assert story_id in scenarios[name][1], f"{story_id} points to '{name}', which does not cover it"
    covered = {story for _, stories in scenarios.values() for story in re.findall(r"US-\d+", stories)}
    for story in covered:
        assert "python scripts/demo.py" in _stories()[story], f"{story} has a scenario the document does not show"
