from __future__ import annotations

import json
import subprocess
import sys
from typing import cast


def test_historical_project_benchmark_keeps_every_read_bounded() -> None:
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "benchmarks",
            "run",
            "historical-project",
            "--runs",
            "250",
            "--project-analyses",
            "100",
            "--page-size",
            "25",
            "--repetitions",
            "1",
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    result_line = next(
        line
        for line in completed.stdout.splitlines()
        if line.startswith("BENCHMARK_RESULT=")
    )
    result = cast(
        "dict[str, object]",
        json.loads(result_line.removeprefix("BENCHMARK_RESULT=")),
    )
    assert result["schema"] == "scopecat.benchmark_result.v1"
    assert result["case_id"] == "historical-project"
    assert result["case_version"] == 1
    assert result["kind"] == "component"
    assert result["run_count"] == 250
    assert result["project_analysis_count"] == 100
    measurements = cast("dict[str, dict[str, object]]", result["measurements"])
    for label in (
        "newest_run_page",
        "oldest_run_page",
        "newest_analysis_page",
        "oldest_analysis_page",
    ):
        assert measurements[label]["returned_count"] == 25
        assert cast("int", measurements[label]["response_bytes"]) < 256 * 1024
    assert (
        measurements["exact_middle_run"]["run_id"]
        == "00000000-0000-7000-8000-00000000007d"
    )
