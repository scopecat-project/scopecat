"""Keep diagnostic failures and bound cleanup to owned processes."""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scopecat_server.host_diagnostics import archive_diagnostics, run_case, summarize


def test_failed_process_is_not_a_success(tmp_path: Path) -> None:
    result = run_case(
        [
            sys.executable,
            "-c",
            "print('original failure', flush=True); raise SystemExit(7)",
        ],
        tmp_path,
        dict(os.environ),
        10,
        cwd=tmp_path,
    )
    assert result["status"] == "failed"
    assert result["returncode"] == 7
    assert "original failure" in (tmp_path / "worker.log").read_text()
    assert json.loads((tmp_path / "outcome.json").read_text()) == result


def test_watchdog_leaves_unrelated_process_alive(tmp_path: Path) -> None:
    sibling = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        result = run_case(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            tmp_path,
            dict(os.environ),
            0.4,
            cwd=tmp_path,
        )
        assert result["watchdog_timeout"] is True
        assert result["status"] == "failed"
        assert result["survivors"] == []
        assert sibling.poll() is None
    finally:
        sibling.terminate()
        sibling.wait(timeout=5)


def test_summary_keeps_partial_failure_and_correlates_first_data(
    tmp_path: Path,
) -> None:
    case = tmp_path / "session-01"
    (case / "timing").mkdir(parents=True)
    events = [
        {"phase": "submit", "status": "started", "clock_ns": 1000000000},
        {"phase": "run", "status": "observed", "run_id": "wanted"},
        {"phase": "prepare_first", "status": "failed", "seconds": 4},
    ]
    (case / "events.jsonl").write_text("\n".join(map(json.dumps, events)))
    nested = [
        {
            "phase": "first_measurement_ingested",
            "run_id": "other",
            "monotonic_ns": 9000000000,
        },
        {
            "phase": "first_measurement_ingested",
            "run_id": "wanted",
            "monotonic_ns": 2500000000,
        },
    ]
    (case / "timing/p.jsonl").write_text("\n".join(map(json.dumps, nested)))
    summary = summarize(
        tmp_path,
        [{"name": "session-01", "status": "failed", "wall_seconds": 5.0}],
        {"expected_cases": 2},
    )
    assert summary["passed"] is False
    assert summary["timings_seconds"] == {
        "submit_to_first_ingest": {
            "samples": [1.5],
            "median": 1.5,
            "min": 1.5,
            "max": 1.5,
        }
    }
    assert summarize(tmp_path, [], {"expected_cases": 1})["passed"] is False


def test_partial_event_does_not_destroy_report(tmp_path: Path) -> None:
    case = tmp_path / "session-01"
    case.mkdir()
    (case / "events.jsonl").write_text('{"phase": "unfinished"')
    report = summarize(
        tmp_path,
        [{"name": "session-01", "status": "passed", "wall_seconds": 1.0}],
        {"expected_cases": 1},
    )
    assert report["passed"] is False
    assert (tmp_path / "report.md").exists()


def test_missing_program_is_preserved_as_failed_outcome(tmp_path: Path) -> None:
    result = run_case(
        [str(tmp_path / "missing-executable")],
        tmp_path,
        dict(os.environ),
        1,
        cwd=tmp_path,
    )
    assert result["status"] == "failed"
    assert result["returncode"] is None
    assert "FileNotFoundError" in (tmp_path / "worker.log").read_text()


@pytest.mark.parametrize(
    "row",
    [
        "null",
        "[]",
        '{"seconds": 1}',
        '{"phase": "prepare", "seconds": "slow"}',
        '{"phase": "prepare", "seconds": NaN}',
    ],
)
def test_malformed_record_fails_without_destroying_report(
    tmp_path: Path, row: str
) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "events.jsonl").write_text(row)
    result = summarize(
        tmp_path,
        [{"name": "case", "status": "passed", "wall_seconds": 1}],
        {"expected_cases": 1},
    )
    assert result["passed"] is False
    assert result["timings_seconds"] == {}
    assert (tmp_path / "report.md").exists()


def test_archive_contains_only_explicit_evidence_without_symlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "diagnostic.v1"
    case = root / "case"
    (case / "timing").mkdir(parents=True)
    (case / "diagnostics").mkdir()
    (case / "project").mkdir()
    (root / "metadata.json").write_text("{}")
    (case / "worker.log").write_text("worker")
    (case / "author-kernel.log").write_text("kernel")
    (case / "timing/run.jsonl").write_text("{}")
    (case / "diagnostics/start.log").write_text("startup")
    (case / "project/secret.txt").write_text("private scientific data")
    (case / "arbitrary.txt").write_text("unrelated")
    secret = tmp_path / "secret.log"
    secret.write_text("secret")
    (case / "diagnostics/leak.log").symlink_to(secret)
    (root / "linked").symlink_to(case, target_is_directory=True)
    archive = archive_diagnostics(root, ["case", "linked"])
    assert archive.name == "diagnostic.v1.zip"
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {
            "metadata.json",
            "case/worker.log",
            "case/author-kernel.log",
            "case/timing/run.jsonl",
            "case/diagnostics/start.log",
        }
    with pytest.raises(FileExistsError):
        archive_diagnostics(root, ["case"])
    with pytest.raises(ValueError, match="single directory"):
        archive_diagnostics(root, ["../outside"])
