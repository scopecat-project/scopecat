"""Keep a small real worker/candidate/verification/publication chain in PR CI."""

from pathlib import Path

from .test_array_maintenance import run_array_maintenance


def test_two_target_calibration_survives_restart_and_publishes(tmp_path: Path) -> None:
    run_array_maintenance(tmp_path, case="healthy", target_count=2)
