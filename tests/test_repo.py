"""The repository stays tidy: what a newcomer expects is there, and nothing that should not be."""
import ast
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_the_files_a_newcomer_looks_for_exist():
    for name in ("README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md", "Makefile", ".gitignore",
                 ".editorconfig", ".github/workflows/tests.yml", ".github/dependabot.yml",
                 ".github/pull_request_template.md", ".github/ISSUE_TEMPLATE/bug_report.md"):
        assert (ROOT / name).is_file(), name


def test_the_core_has_light_dependencies_and_heavier_ones_are_extras():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert sorted(d.split(">")[0] for d in meta["dependencies"]) == ["httpx", "pydantic"]
    extras = meta["optional-dependencies"]
    assert {"claude", "fastapi", "server", "dev"} <= set(extras)
    assert any(d.startswith("pytest") for d in extras["dev"]) and any(d.startswith("ruff") for d in extras["dev"])
    assert not (ROOT / "requirements.txt").exists()  # one source of truth: pyproject.toml


def test_ci_installs_the_dev_extra_and_has_least_privilege():
    ci = (ROOT / ".github/workflows/tests.yml").read_text()
    assert ".[dev]" in ci
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
    junk = re.compile(r"(__pycache__|\.pyc$|\.db$|\.env$|\.DS_Store|\.ruff_cache|^build/|^dist/|egg-info)")
    junk = [f for f in tracked if junk.search(f)]
    assert junk == []


def test_main_stays_a_thin_assembly_module():
    assert len((ROOT / "support_copilot/main.py").read_text().splitlines()) < 60


def test_every_third_party_import_of_the_core_is_a_declared_dependency():
    """A wheel installed on its own must import: this is what failed when httpx was only a dev dependency."""
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    declared = {d.split(">")[0].split("[")[0] for d in meta["dependencies"]}
    optional = {"fastapi", "uvicorn", "anthropic"}  # imported only by api.py, main.py or lazily
    imported = set()
    for path in (ROOT / "support_copilot").rglob("*.py"):
        if path.name in {"api.py", "main.py"}:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module else []
            imported |= {n.split(".")[0] for n in names}
    third_party = imported - set(sys.stdlib_module_names) - {"support_copilot"} - optional
    assert third_party <= declared, f"undeclared: {third_party - declared}"
