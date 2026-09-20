"""Run an explicitly selected diagnostic workload without laboratory imports."""

from __future__ import annotations

import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import JsonValue

from scopecat_server.host_diagnostics import (
    archive_diagnostics,
    run_case,
    summarize,
    write_json,
)


def diagnose_command(
    command: list[str], *, output: Path, cwd: Path, timeout: float
) -> bool:
    """Retain one command's evidence; never select an experiment implicitly."""
    root = output.resolve()
    archive_path = root.with_name(root.name + ".zip")
    if archive_path.exists():
        raise FileExistsError(f"Diagnostic archive already exists: {archive_path}")
    root.mkdir(parents=True, exist_ok=False)
    directory = root / "command"
    directory.mkdir()
    (directory / "timing").mkdir()
    metadata: dict[str, JsonValue] = {
        "expected_cases": 1,
        "utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "executable": sys.executable,
        "command": list(command),
        "cwd": str(cwd.resolve()),
        "case_timeout_seconds": timeout,
        "workload": "Explicit user command; no implicit laboratory or device selection",
    }
    write_json(root / "metadata.json", metadata)
    environment = dict(os.environ)
    environment.update(
        SCOPECAT_STARTUP_DIAGNOSTICS=str(directory / "diagnostics"),
        SCOPECAT_TIMING_DIRECTORY=str(directory / "timing"),
        SCOPECAT_DIAGNOSTIC_DIRECTORY=str(directory),
    )
    cases: list[dict[str, JsonValue]] = []
    try:
        result = run_case(command, directory, environment, timeout, cwd=cwd)
        cases.append({"name": "command", **result})
    finally:
        summary = summarize(root, cases, metadata)
        archive = archive_diagnostics(root, ["command"])
        print(f"Report: {root / 'report.md'}\nArchive: {archive}")
    return summary["passed"] is True
