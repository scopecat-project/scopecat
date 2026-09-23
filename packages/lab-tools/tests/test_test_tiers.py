"""The ordinary gate and qualification shards must cover every test file."""

from pathlib import Path

import pytest

from scopecat_testkit.check import select_files


def test_repository_tiers_preserve_qualification_coverage() -> None:
    root = Path(__file__).resolve().parents[3]
    core = set(select_files(root, "core"))
    journey = set(select_files(root, "journey"))
    assert core.isdisjoint(journey)
    assert core | journey == set(select_files(root, "full"))
    assert "packages/lab-tools/tests/test_calibration_smoke.py" in core
    assert "packages/lab-tools/tests/test_array_maintenance.py" in journey
    assert "packages/lab-tools/tests/test_installed_adapter_journey.py" in journey
    first = set(select_files(root, "journey", (1, 2)))
    second = set(select_files(root, "journey", (2, 2)))
    assert first.isdisjoint(second)
    assert first | second == journey


@pytest.mark.parametrize("suite", ["fast", "core", "journey", "full"])
def test_unclassified_files_require_a_decision(tmp_path: Path, suite: str) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.scopecat-tests]\nroots = ["tests"]\n'
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_new_flow.py").touch()
    with pytest.raises(ValueError, match="Unclassified test file: tests/test_new_flow"):
        select_files(tmp_path, suite)
