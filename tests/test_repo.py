"""The repository stays tidy: what a newcomer expects is there, and nothing that should not be."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_the_files_a_newcomer_looks_for_exist():
    for name in ("README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md", "Makefile", ".gitignore",
                 ".editorconfig", ".github/workflows/tests.yml", ".github/dependabot.yml",
                 ".github/pull_request_template.md", ".github/ISSUE_TEMPLATE/bug_report.md"):
        assert (ROOT / name).is_file(), name


def test_runtime_and_development_requirements_are_separate():
    runtime = (ROOT / "requirements.txt").read_text().lower()
    dev = (ROOT / "requirements-dev.txt").read_text().lower()
    assert "pytest" not in runtime and "ruff" not in runtime
    assert "-r requirements.txt" in dev and "pytest" in dev and "ruff" in dev
    assert all(pkg in runtime for pkg in ("fastapi", "uvicorn", "pydantic", "anthropic"))


def test_ci_installs_the_dev_requirements_and_has_least_privilege():
    ci = (ROOT / ".github/workflows/tests.yml").read_text()
    assert "requirements-dev.txt" in ci
    assert re.search(r"^permissions:\s*\n\s+contents: read", ci, re.M)


def test_every_doc_is_linked_from_the_readme():
    readme = (ROOT / "README.md").read_text()
    for doc in sorted((ROOT / "docs").glob("*.md")):
        assert f"docs/{doc.name}" in readme, f"{doc.name} is not linked from the README"


@pytest.mark.skipif(not shutil.which("make"), reason="make is not installed")
def test_every_makefile_command_is_valid_and_documented():
    makefile = (ROOT / "Makefile").read_text()
    targets = re.findall(r"^([a-z]+):.*##", makefile, re.M)
    assert {"install", "run", "test", "lint", "check"} <= set(targets)
    for target in targets:
        assert subprocess.run(["make", "-n", target], cwd=ROOT, capture_output=True).returncode == 0, target


@pytest.mark.skipif(not (ROOT / ".git").exists() or not shutil.which("git"), reason="not a git checkout")
def test_no_junk_is_tracked():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    junk = [f for f in tracked if re.search(r"(__pycache__|\.pyc$|\.db$|\.env$|\.DS_Store|\.ruff_cache)", f)]
    assert junk == []


def test_main_stays_a_thin_assembly_module():
    assert len((ROOT / "app/main.py").read_text().splitlines()) < 60
