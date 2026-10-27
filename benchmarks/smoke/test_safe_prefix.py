from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from benchmarks.component.safe_prefix import _options  # pyright: ignore[reportPrivateUsage]

_ROOT = Path(__file__).resolve().parents[2]


def _run(data_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "benchmarks",
            "run",
            "safe-prefix",
            "--target-repo",
            str(_ROOT),
            "--target-python",
            sys.executable,
            "--data-root",
            str(data_root),
            "--points",
            "8",
            "--label",
            "smoke",
        ],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_safe_prefix_commits_tail_and_reports_versioned_costs(tmp_path: Path) -> None:
    data_root = tmp_path / "new-synthetic-data"
    result = _run(data_root)
    assert result.returncode == 0, result.stderr
    record = cast(
        "dict[str, object]",
        json.loads(
            next(
                line.removeprefix("BENCHMARK_RESULT=")
                for line in result.stdout.splitlines()
                if line.startswith("BENCHMARK_RESULT=")
            )
        ),
    )
    assert record["schema"] == "scopecat.benchmark_result.v1"
    assert (record["case_id"], record["case_version"], record["kind"]) == (
        "safe-prefix",
        1,
        "component",
    )
    assert record["durable_records"] == record["completed_prefix"] == 8
    assert 2 <= cast("int", record["measurement_transactions"]) <= 8
    assert cast("int", record["write_transactions"]) > cast(
        "int", record["measurement_transactions"]
    )
    assert cast("int", record["coverage_events"]) >= 2
    assert cast("int", record["encoded_append_bytes"]) > 0
    assert cast("int", record["observed_project_bytes"]) > 0
    assert record["python"] == "3.15.1"
    assert record["benchmark"] == record["target"]
    identity = cast("dict[str, object]", record["target"])
    assert len(cast("str", identity["sha"])) == 40
    assert len(cast("str", identity["uv_lock_sha256"])) == 64
    assert not data_root.exists()


def test_safe_prefix_rejects_existing_directory_without_touching_it(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["keep.txt"]


@pytest.mark.parametrize("points", ["0", "4097"])
def test_safe_prefix_rejects_unbounded_workload(points: str, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _options(
            [
                "--target-repo",
                str(_ROOT),
                "--target-python",
                sys.executable,
                "--data-root",
                str(tmp_path / "unused"),
                "--points",
                points,
            ]
        )
    assert not (tmp_path / "unused").exists()
