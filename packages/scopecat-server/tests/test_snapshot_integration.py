"""The installed-project checker also runs in the ordinary Python test gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_reference_lab_snapshot_roundtrip_in_fresh_processes() -> None:
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(  # noqa: S603 - fixed interpreter and local test fixture
        [
            sys.executable,
            str(root / "packages/scopecat-server/tests/fixtures/snapshot_roundtrip.py"),
            str(root / "examples/reference_lab"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
