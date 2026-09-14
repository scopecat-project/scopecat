from __future__ import annotations

import subprocess
import sys


def test_daemon_wire_does_not_import_native_analysis_runtimes() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import scopecat.daemon.wire

forbidden = {
    "pandas",
    "pyarrow",
    "scopecat.analysis.datasets",
    "xarray",
}
loaded = forbidden.intersection(sys.modules)
if loaded:
    raise SystemExit(f"wire models imported native runtimes: {sorted(loaded)}")
""",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_measurement_authoring_does_not_import_dataframe_views() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from scopecat.measurements.results import Dataset, PointMask, ProjectionSchema
from scopecat.api.analysis import analysis_function

loaded = {"pandas", "xarray"}.intersection(sys.modules)
if loaded:
    raise SystemExit(f"authoring imported dataframe views: {sorted(loaded)}")
""",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
