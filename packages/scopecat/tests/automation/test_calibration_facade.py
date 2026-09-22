"""Keep retired cohort authoring out of the ordinary notebook entry point."""

import subprocess
import sys


def test_notebook_client_does_not_assemble_legacy_calibration_runtime() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import scopecat
from scopecat.api.lab import LabClient
from scopecat.daemon.client import DaemonClient

with DaemonClient("http://daemon.test") as daemon:
    lab = LabClient(daemon)
    assert lab.procedures is not None
    assert not hasattr(lab, "calibrations")

assert "calibration" not in scopecat.__all__
assert not any(name.startswith("Calibration") for name in scopecat.__all__)
for module in (
    "scopecat.api.calibrations",
    "scopecat.api.calibration_planner",
    "scopecat.api.calibration_finalizer",
    "scopecat.api.calibration_policy",
):
    assert module not in sys.modules, module
""",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
