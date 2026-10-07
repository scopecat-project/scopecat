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
    for suite in ("core", "journey"):
        first = set(select_files(root, suite, (1, 2)))
        second = set(select_files(root, suite, (2, 2)))
        assert first and second
        assert first.isdisjoint(second)
        assert first | second == set(select_files(root, suite))


@pytest.mark.parametrize("suite", ["fast", "core", "journey", "full"])
def test_unclassified_files_require_a_decision(tmp_path: Path, suite: str) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.scopecat-tests]\nroots = ["tests"]\n'
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_new_flow.py").touch()
    with pytest.raises(ValueError, match="Unclassified test file: tests/test_new_flow"):
        select_files(tmp_path, suite)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("pass", 0),
        ("import pytest; pytest.skip('missing native dependency')", 1),
        ("import pytest; pytest.xfail('not coverage')", 1),
        ("assert False", 1),
    ],
)
def test_platform_smoke_requires_actual_passes(tmp_path, monkeypatch, body, expected):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from scripts import platform_smoke

    test = tmp_path / "test_probe.py"
    test.write_text(f"def test_probe():\n    {body}\n", encoding="utf-8")
    # Keep the child platform real; only select the probe in the runner parent.
    monkeypatch.setattr(platform_smoke, "COMMON", [str(test) + "::test_probe"])
    monkeypatch.setattr(platform_smoke, "PLATFORM", {platform_smoke.sys.platform: []})
    monkeypatch.delenv("SMOKE_EXPECTED_SHA", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        platform_smoke.subprocess, "check_output", lambda *_a, **_kw: "test-sha"
    )
    # This temporary probe has no xdist config; main still supplies -n 0.
    assert platform_smoke.main() == expected
