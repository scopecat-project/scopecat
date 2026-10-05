"""Run the maintained platform selection; skips cannot count as coverage."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree

COMMON = [
    "packages/scopecat-instruments/tests/test_sdk_process.py::test_sdk_binary_arrays_cancellation_and_owned_exit",
    "packages/scopecat-server/tests/test_instrument_worker.py::test_spawned_worker_executes_closed_driver_requests",
    "packages/scopecat-server/tests/test_instrument_worker.py::test_driver_uses_selected_python_and_retains_it_after_reopen",
    "packages/scopecat-server/tests/test_instrument_worker.py::test_shutdown_interrupts_a_blocked_driver_call",
    "packages/lab-tools/tests/test_platform_files.py::test_application_lock_excludes_process_and_state_replaces",
]
PLATFORM = {
    "darwin": [
        "packages/lab-tools/tests/test_desktop_platform.py::test_quit_hook_matches_real_pywebview_delegate_signature"
    ],
    "win32": [
        "packages/lab-tools/tests/test_platform_environment.py::test_prepared_execution_python_imports_pyarrow"
    ],
}


def main() -> int:
    if sys.platform not in PLATFORM:
        raise SystemExit("Platform smoke requires a macOS or Windows runner")
    selected = COMMON + PLATFORM[sys.platform]
    report = Path(".test-results/platform-smoke")
    report.mkdir(parents=True, exist_ok=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()  # noqa: S607
    expected = os.environ.get("SMOKE_EXPECTED_SHA", revision)
    if revision != expected:
        raise SystemExit(f"Expected {expected}, checked out {revision}")
    xml = report / "results.xml"
    xml.unlink(missing_ok=True)
    started = time.monotonic()
    result = subprocess.run(  # noqa: S603 - maintained nodeids, current Python
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            "0",
            "--durations=20",
            f"--junitxml={xml}",
            *selected,
        ],
        check=False,
    )
    cases = ElementTree.parse(xml).findall(".//testcase") if xml.exists() else []  # noqa: S314 - local pytest output
    skipped = sum(case.find("skipped") is not None for case in cases)
    failed = sum(
        case.find("failure") is not None or case.find("error") is not None
        for case in cases
    )
    evidence = {
        "revision": revision,
        "platform": platform.platform(),
        "python": sys.version,
        "runner": os.environ.get("RUNNER_NAME"),
        "image": os.environ.get("ImageVersion"),  # noqa: SIM112 - runner-defined name
        "selected": selected,
        "tests": len(cases),
        "skipped": skipped,
        "failed": failed,
        "seconds": time.monotonic() - started,
        "pytest_exit": result.returncode,
    }
    (report / "evidence.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2), flush=True)
    return int(
        result.returncode != 0
        or len(cases) != len(selected)
        or skipped != 0
        or failed != 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
