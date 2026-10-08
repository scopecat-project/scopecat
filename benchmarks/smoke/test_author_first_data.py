"""Interaction origins and identity correlation, without starting a laboratory."""

from typing import cast

import pytest

from benchmarks.e2e.author_first_data import correlate_events


def trace(procedure: str, run: str) -> list[dict[str, object]]:
    phases = [
        "procedure_dispatch",
        "procedure_python_entry",
        "procedure_registered",
        "procedure_worker_entry",
        "framework_ready",
        "source_restore_start",
        "application_load_start",
        "application_ready",
        "run_admitted",
        "first_measurement_ready",
        "first_measurement_ingested",
        "run_terminal_committed",
        "client_result_visible",
    ]
    return [
        {
            "phase": phase,
            "monotonic_ns": 3_000_000_000 + index * 100_000_000,
            **({"procedure_id": procedure} if index < 8 else {"run_id": run}),
        }
        for index, phase in enumerate(phases)
    ]


def sample() -> dict[str, object]:
    return {
        "procedure_id": "selected-procedure",
        "run_id": "selected-run",
        # Includes refresh/prepare before submission; never hide that cost.
        "interaction_start_ns": 1_000_000_000,
        "submit_start_ns": 3_000_000_000,
    }


def test_correlates_process_traces_and_preserves_both_origins() -> None:
    selected = sample()
    expected = trace("selected-procedure", "selected-run")
    mixed = [*reversed(expected), *trace("other-procedure", "other-run")]
    correlate_events([selected], mixed)
    events = cast("list[dict[str, object]]", selected["events"])
    assert len(events) == len(expected)
    assert [event["monotonic_ns"] for event in events] == [
        event["monotonic_ns"] for event in expected
    ]
    assert events[0]["since_submit_seconds"] == 0
    assert events[0]["since_interaction_seconds"] == 2
    assert events[-1]["since_submit_seconds"] == 1.2
    assert events[-1]["since_interaction_seconds"] == 3.2
    assert "since_submit_seconds" not in expected[0]


def test_other_runs_cannot_fill_a_missing_boundary() -> None:
    events = trace("selected-procedure", "selected-run")[:-1]
    events += trace("other-procedure", "other-run")
    with pytest.raises(AssertionError, match="client_result_visible"):
        correlate_events([sample()], events)
