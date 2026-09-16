"""Run typed notebook rebinding and scalar restart acceptance in a fresh client."""

import subprocess
import sys
from pathlib import Path


def test_typed_author_refresh_and_retained_results() -> None:
    root = Path(__file__).resolve().parents[3]
    subprocess.run(  # noqa: S603 - repository-owned acceptance entry point
        [sys.executable, str(root / "scripts/verify_scalar_author_result.py")],
        cwd=root,
        check=True,
        timeout=180,
    )
