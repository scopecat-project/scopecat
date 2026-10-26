"""Six targets retain partial progress, recover, and publish only complete evidence."""

from pathlib import Path

import pytest

from .support import run_array_maintenance


@pytest.mark.parametrize(
    "case",
    [
        "healthy",
        "drift",
        "readout-failure",
        "branch-conflict",
        "repair",
        "repair-budget",
        "repair-failure",
        "repair-drift",
        "repair-conflict",
        "repair-verification",
        "repair-noop",
    ],
)
def test_array_maintenance(tmp_path: Path, case: str) -> None:
    run_array_maintenance(tmp_path, case=case, target_count=6)
