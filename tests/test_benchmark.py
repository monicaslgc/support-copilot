"""scripts/benchmark.py reproduces the numbers in docs/engineering.md."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("benchmark", ROOT / "scripts" / "benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_it_prints_the_versions_the_blocks_and_the_listing_times(capsys):
    assert benchmark.main(["--tickets", "200"]) == 0
    out = capsys.readouterr().out
    assert "python " in out and "sqlite " in out and "N =   100:" in out and "N =   200:" in out
    assert "listing the 100 most urgent tickets:" in out


def test_it_refuses_a_run_too_small_to_mean_anything():
    with pytest.raises(SystemExit, match="at least 100"):
        benchmark.main(["--tickets", "50"])
