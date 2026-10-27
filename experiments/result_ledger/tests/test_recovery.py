"""Real CLI process exits/reopens at each external-effect boundary."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def command(*args: str, expected: int = 0) -> dict:
    result = subprocess.run(  # noqa: S603 - fixed local module and test-generated arguments
        [sys.executable, "-m", "experiments.result_ledger", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == expected, (result.stdout, result.stderr)
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "boundary", ["provider-return", "result-save", "group-publish", "notification"]
)
@pytest.mark.parametrize(
    "hard_crash", [False, True], ids=["controlled-exit", "process-death"]
)
def test_process_boundary_recovery_preserves_unknown_and_does_not_repeat_saved_work(
    tmp_path, boundary, hard_crash
):
    directory = str(tmp_path / boundary)
    command(
        "demo",
        "--directory",
        directory,
        "--commit-mode",
        "staged",
        "--crash-at",
        boundary,
        *(["--hard-crash"] if hard_crash else []),
        expected=75,
    )
    interrupted = command("inspect", "--directory", directory)
    assert len(interrupted["provider_calls"]) == 1
    if boundary == "provider-return":
        assert interrupted["counts"]["results"] == 0
        command("recover", "--directory", directory, expected=3)
        blocked = command("inspect", "--directory", directory)
        assert len(blocked["provider_calls"]) == 1
        assert blocked["attempts"][0]["status"] == "unknown"
        complete = command("recover", "--directory", directory, "--authorize-retry")
    else:
        assert interrupted["counts"]["results"] == 1
        complete = command("recover", "--directory", directory)
    assert complete["status"] == "completed"
    assert complete["counts"] == {
        "points": 6,
        "units": 3,
        "attempts": 4 if boundary == "provider-return" else 3,
        "results": 3,
        "selections": 3,
        "snapshots": 3,
    }
    assert len(complete["provider_calls"]) == complete["counts"]["attempts"]
    assert len(complete["events"]) == len(complete["deliveries"]) == 3
    assert complete["notification_cursor"] == complete["events"][-1]["seq"]
    assert complete["events"][0]["payload"]["group_id"] == "high"
    assert complete["events"][-1]["payload"]["group_id"] == "adaptive"
    if boundary == "notification":
        assert complete["deliveries"][0]["delivery_attempts"] == 2
    # Repeat recovery adds no acquisitions, publications or analysis identities.
    again = command("recover", "--directory", directory)
    for key in (
        "provider_calls",
        "results",
        "selections",
        "snapshots",
        "events",
        "deliveries",
        "analyses",
    ):
        assert again[key] == complete[key]
    assert_exact_analysis(complete)


def assert_exact_analysis(state: dict) -> None:
    snapshots = {snapshot["id"]: snapshot for snapshot in state["snapshots"]}
    assert len(state["analyses"]) == 3
    observed_partial = False
    for analysis in state["analyses"]:
        snapshot = snapshots[analysis["snapshot_id"]]
        encoded = json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        assert analysis["input_sha256"] == hashlib.sha256(encoded.encode()).hexdigest()
        expected = []
        for bundle in snapshot["bundles"]:
            for block in bundle["outputs"]:
                if block["id"] != "waveform":
                    continue
                points, channels, samples = block["shape"]
                for point_index in range(points):
                    for channel in range(channels):
                        start = (point_index * channels + channel) * samples
                        valid = [
                            block["values"][index]
                            for index in range(start, start + samples)
                            if block["availability"][index]
                        ]
                        expected.append(
                            (
                                block["point_ids"][point_index],
                                block["coordinates"]["channel"]["values"][channel],
                                sum(valid) / len(valid),
                                len(valid),
                                samples,
                                block["unit"],
                                bundle["hash"],
                            )
                        )
                        observed_partial |= len(valid) < samples
        actual = [
            (
                row["point_id"],
                row["channel"],
                row["mean"],
                row["available_samples"],
                row["expected_samples"],
                row["unit"],
                row["bundle_hash"],
            )
            for row in analysis["rows"]
        ]
        assert actual == expected
    assert observed_partial


def test_inspect_never_takes_a_fence_or_mutates_records_and_demo_refuses_existing_data(
    tmp_path,
):
    directory = str(tmp_path / "demo")
    complete = command("demo", "--directory", directory)
    before = command("inspect", "--directory", directory)
    after = command("inspect", "--directory", directory)
    assert before == after
    assert before["fence"] == complete["fence"]
    result = subprocess.run(  # noqa: S603 - fixed local module and test-generated arguments
        [
            sys.executable,
            "-m",
            "experiments.result_ledger",
            "demo",
            "--directory",
            directory,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert "existing data is never replaced" in result.stderr
    assert command("inspect", "--directory", directory) == before
