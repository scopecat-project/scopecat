"""Selection must partition full verification without dropping new files."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Literal

import pytest
from scopecat_testkit.check import select_files
from scopecat_testkit.pytest_timing import TimingReport


def test_tiers_and_weighted_shards_cover_every_file_once(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("""
[tool.scopecat-tests]
roots = ["tests"]
fast_paths = ["tests/journey/unit"]
integration_paths = ["tests/integration"]
journey_paths = ["tests/journey", "tests/integration/test_restart.py"]
[tool.scopecat-tests.weights]
"tests/journey/test_long.py" = 100
"tests/integration/test_restart.py" = 80
""")
    files = {
        "tests/test_new.py",
        "tests/suffix_test.py",
        "tests/integration/test_api.py",
        "tests/integration/test_restart.py",
        "tests/journey/test_long.py",
        "tests/journey/test_short.py",
        "tests/journey/unit/test_compiler.py",
    }
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    fast = set(select_files(tmp_path, "fast"))
    integration = set(select_files(tmp_path, "integration"))
    journey = set(select_files(tmp_path, "journey"))
    assert fast == {
        "tests/test_new.py",
        "tests/suffix_test.py",
        "tests/journey/unit/test_compiler.py",
    }
    assert integration == {"tests/integration/test_api.py"}
    assert fast | integration | journey == files == set(select_files(tmp_path, "full"))
    assert set(select_files(tmp_path, "core")) == fast | integration
    first = set(select_files(tmp_path, "journey", (1, 2)))
    second = set(select_files(tmp_path, "journey", (2, 2)))
    assert not first & second
    assert first | second == journey
    assert "tests/journey/test_long.py" in first
    assert "tests/integration/test_restart.py" in second


def test_timing_report_preserves_setup_call_teardown_and_failures(
    tmp_path: Path,
) -> None:
    target = tmp_path / "timing.json"
    collector = TimingReport(target)
    phases: tuple[
        tuple[Literal["setup", "call", "teardown"], float, Literal["passed", "failed"]],
        ...,
    ] = (
        ("setup", 1.0, "passed"),
        ("call", 2.0, "failed"),
        ("teardown", 3.0, "passed"),
    )
    for phase, seconds, outcome in phases:
        collector.pytest_runtest_logreport(
            pytest.TestReport(
                nodeid="test_run",
                location=("test_run.py", 0, "test_run"),
                keywords={},
                outcome=outcome,
                longrepr=None,
                when=phase,
                duration=seconds,
            )
        )
    collector.pytest_sessionfinish(1)
    data = json.loads(target.read_text())
    assert data["exitstatus"] == 1
    assert data["tests"][0]["total_seconds"] == 6
    assert data["tests"][0]["phases"] == {"setup": 1, "call": 2, "teardown": 3}
    assert data["tests"][0]["outcomes"]["call"] == "failed"


def test_repository_tiers_cover_pytest_roots() -> None:
    root = Path(__file__).resolve().parents[3]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    assert set(config["tool"]["scopecat-tests"]["roots"]) == set(
        config["tool"]["pytest"]["testpaths"]
    )
    unit_files = {
        path.relative_to(root).as_posix()
        for path in (root / "examples/reference_lab/tests/unit").glob("test_*.py")
    }
    assert unit_files <= set(select_files(root, "fast"))
    assert not unit_files & set(select_files(root, "journey"))


def test_runner_keeps_workspace_config_for_a_nested_only_tier(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("""
[tool.pytest.ini_options]
addopts = ["--import-mode=importlib"]
[tool.scopecat-tests]
roots = ["nested/tests"]
""")
    tests = tmp_path / "nested/tests"
    tests.mkdir(parents=True)
    (tests.parent / "pyproject.toml").write_text("""
[tool.pytest.ini_options]
addopts = ["-k", "this_would_deselect_everything"]
""")
    (tests / "test_one.py").write_text("def test_one(): pass\n")
    result = subprocess.run(  # noqa: S603 - controlled test runner and fixture
        [
            sys.executable,
            "-m",
            "scopecat_testkit.check",
            "fast",
            "--root",
            str(tmp_path),
            "--report-dir",
            str(tmp_path / "reports"),
            "--",
            "-q",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    timing = json.loads((tmp_path / "reports/fast-1-of-1.json").read_text())
    assert timing["tests"][0]["nodeid"] == "nested/tests/test_one.py::test_one"
