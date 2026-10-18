"""The Dockerfile and compose file are checked as text: the images could not be built where this was written
(CI builds the image and calls /health). These tests keep the files from drifting from the app."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
API_DOC = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")


def test_the_container_does_not_run_as_root():
    users = re.findall(r"(?m)^USER (\S+)", DOCKERFILE)
    assert users and users[-1] not in ("root", "0")


def test_the_server_runs_one_worker_and_without_reload():
    cmd = re.search(r"(?m)^CMD (.+)$", DOCKERFILE).group(1)
    assert "support_copilot.main:app" in cmd and "--host" in cmd
    assert "--reload" not in cmd and "--workers" not in cmd      # one SQLite connection behind a lock


def test_the_database_lives_on_a_volume_the_user_can_write():
    assert "TRIAGE_DB=/data/triage.db" in DOCKERFILE and re.search(r"(?m)^VOLUME /data", DOCKERFILE)
    assert re.search(r"chown \S+ /data", DOCKERFILE)
    assert "copilot-data:/data" in COMPOSE and re.search(r"(?m)^volumes:\n\s+copilot-data:", COMPOSE)


def test_the_healthcheck_calls_the_health_route():
    assert "HEALTHCHECK" in DOCKERFILE and "/health" in DOCKERFILE
    assert '@app.get("/health"' in (ROOT / "support_copilot" / "main.py").read_text(encoding="utf-8")


def test_the_image_installs_the_server_extra_and_what_it_needs():
    assert 'pip install ".[server]"' in DOCKERFILE
    for needed in ("pyproject.toml", "README.md", "LICENSE", "support_copilot"):   # setuptools reads these
        assert needed in DOCKERFILE and (ROOT / needed).exists()


def test_compose_requires_an_api_key_and_listens_on_this_machine_only():
    assert re.search(r"API_KEY=\$\{API_KEY:\?", COMPOSE)
    assert '"127.0.0.1:8000:8000"' in COMPOSE


def test_compose_passes_only_variables_the_docs_describe_and_none_as_empty_strings():
    block = COMPOSE.split("environment:")[1].split("volumes:")[0]
    names = re.findall(r"(?m)^\s+- ([A-Z][A-Z0-9_]+)", block)
    assert "ALLOWED_REVIEWERS" in names
    for name in names:
        assert f"`{name}`" in API_DOC, f"{name} is passed to the container but not in docs/api.md"
    # "NAME=${NAME:-}" would hand the app an empty string, which an empty ALLOWED_REVIEWERS turns into an error
    assert not re.search(r":-\}", COMPOSE)


def test_the_build_context_leaves_out_secrets_and_data():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert {".env", "*.db", ".git"} <= set(ignored)


def test_ci_builds_the_image_and_calls_health():
    ci = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "docker build" in ci and "/health" in ci
