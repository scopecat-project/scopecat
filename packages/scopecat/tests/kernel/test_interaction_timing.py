"""Optional diagnostics cannot turn a successful operation into a failure."""

import json
from pathlib import Path

import pytest

from scopecat.kernel.interaction_timing import TIMING_DIRECTORY_ENV, record_timing


def test_timing_is_opt_in_and_preserves_event_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(TIMING_DIRECTORY_ENV, raising=False)
    record_timing("disabled", run_id="unrecorded")
    assert not tuple(tmp_path.iterdir())
    monkeypatch.setenv(TIMING_DIRECTORY_ENV, str(tmp_path))
    record_timing("first_measurement_ready", run_id="run-one")
    record_timing("first_measurement_ingested", run_id="run-one")
    (path,) = tmp_path.glob("timing-*.jsonl")
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [event["phase"] for event in events] == [
        "first_measurement_ready",
        "first_measurement_ingested",
    ]
    assert {event["run_id"] for event in events} == {"run-one"}
    assert events[0]["monotonic_ns"] <= events[1]["monotonic_ns"]


def test_unwritable_timing_does_not_change_execution_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Missing parent is deterministic even when the test has elevated permissions.
    monkeypatch.setenv(TIMING_DIRECTORY_ENV, str(tmp_path / "missing"))
    record_timing("run_terminal_committed", run_id="already-committed")
    assert "Interaction timing unavailable" in caplog.text
