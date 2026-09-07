# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import cast

import pytest

from benchmarks.e2e.scan_execution import ScanScenario, _scopecat_invocation
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.program.expressions import PointColumnScalarExpr
from scopecat.program.logical import LogicalDomainExecution, LogicalEnsureState


@pytest.mark.parametrize("qubit_count", [2, 16, 64, 128])
def test_multiqubit_derived_results_preserve_the_source_entity_axis(
    qubit_count: int,
) -> None:
    scenario = ScanScenario(
        point_count=1,
        profile="multiqubit_result_retention",
        retention="iq-and-bits",
        qubit_count=qubit_count,
        physical_channel_count=2 * qubit_count + 2,
        shots=4,
    )

    logical = compile_invocation(_scopecat_invocation(scenario)).program.program
    products = {
        product.qualified_id: product for product in logical.product_declarations
    }
    source = products["multiqubit-results/iq_shots"]
    derived = products["entity-bit-shots"]

    assert [
        (
            axis.id,
            type(axis.size),
            axis.kind,
            axis.unit,
            axis.entity_values,
            axis.shared_as,
        )
        for axis in derived.axes
    ] == [
        (
            axis.id,
            type(axis.size),
            axis.kind,
            axis.unit,
            axis.entity_values,
            axis.shared_as,
        )
        for axis in source.axes
    ]
    assert derived.axes[1].size == source.axes[1].size == 4
    assert derived.axes[0].kind == "entity"
    assert derived.axes[0].entity_values
    assert derived.axes[0].shared_as == "targets"


def test_fixed_program_lo_sweep_keeps_the_scan_value_outside_domain_inputs() -> None:
    scenario = ScanScenario(
        point_count=33,
        profile="fixed_program_host_lo_sweep",
        qubit_count=1,
        physical_channel_count=4,
    )

    logical = compile_invocation(_scopecat_invocation(scenario)).program.program
    value_sources = {value.id: value.source for value in logical.value_defs}
    [domain] = [
        effect
        for effect in logical.effects
        if isinstance(effect, LogicalDomainExecution)
    ]
    host_state = [
        effect for effect in logical.effects if isinstance(effect, LogicalEnsureState)
    ]

    assert all(
        not isinstance(value_sources[value_id], PointColumnScalarExpr)
        for _name, value_id in domain.inputs
    )
    assert any(
        assignment.property_id == "frequency"
        and isinstance(value_sources[assignment.value_id], PointColumnScalarExpr)
        for effect in host_state
        for assignment in effect.assignments
    )


def test_fixed_program_lo_sweep_avoids_per_point_durable_job_writes(
    tmp_path: Path,
) -> None:
    point_count = 33
    work_dir = tmp_path / "fixed-program-lo-sweep"
    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            "-m",
            "benchmarks",
            "run",
            "scan-execution",
            "--worker",
            "scopecat",
            "--point-count",
            str(point_count),
            "--qubit-count",
            "1",
            "--profile",
            "lo-sweep",
            "--host-label",
            "test",
            "--work-dir",
            str(work_dir),
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
    scenario = cast("dict[str, object]", result["scenario"])
    database = next(work_dir.rglob("control.sqlite3"))
    with sqlite3.connect(database) as connection:
        domain_transition_count = cast(
            "tuple[int]",
            connection.execute(
                "SELECT COUNT(*) FROM execution_domain_job_transitions"
            ).fetchone(),
        )[0]
        measurement_append_count = cast(
            "tuple[int]",
            connection.execute(
                "SELECT COUNT(*) FROM execution_measurement_appends"
            ).fetchone(),
        )[0]
        durable_event_count = cast(
            "tuple[int]",
            connection.execute("SELECT COUNT(*) FROM durable_events").fetchone(),
        )[0]
        measured_payloads = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload_json FROM durable_events "
                "WHERE kind = 'run_hardware_batch_measured'"
            )
        ]
        finalization_count = connection.execute(
            "SELECT COUNT(*) FROM durable_events "
            "WHERE kind = 'run_hardware_finalization_measured'"
        ).fetchone()[0]
        durable_refs = tuple(
            row[0]
            for row in cast(
                "list[tuple[str]]",
                connection.execute(
                    "SELECT ref FROM run_repository_refs ORDER BY ref"
                ).fetchall(),
            )
        )

    assert result["case_version"] == 9
    assert scenario["profile"] == "fixed_program_host_lo_sweep"
    assert result["points_completed"] == point_count
    assert result["trigger_count"] == point_count
    # Host LO writes invalidate this target's setup residency. Reusing the
    # compiled program must not be mistaken for retaining its device setup.
    expected_upload_bytes = point_count * cast(
        "int", result["max_waveform_batch_bytes"]
    )
    assert result["waveform_bytes_uploaded"] == expected_upload_bytes
    assert result["waveform_bytes_rendered"] == expected_upload_bytes
    assert result["payload_spool_bytes_at_finish"] == 0
    assert (
        0
        < cast("int", result["peak_payload_spool_bytes"])
        <= 2 * cast("int", result["max_waveform_batch_bytes"])
    )
    assert domain_transition_count == 0
    assert measurement_append_count == 1
    # Measurement events follow physical batches, not 1,000 shots per point.
    # Existing lifecycle/domain/measurement ledgers retain their previous bound.
    assert len(measured_payloads) == 5 * point_count + 1
    assert finalization_count == 1
    assert (
        durable_event_count - len(measured_payloads) - finalization_count < point_count
    )
    operation_counts = Counter(
        item["operation"] for payload in measured_payloads for item in payload["costs"]
    )
    assert operation_counts == {
        "invoke": 9 * point_count,
        "apply": point_count + 5,
        "prepare": point_count,
        "collect": point_count,
    }
    assert cast("int", result["durable_file_count"]) < point_count
    assert not any("program" in ref or "waveform" in ref for ref in durable_refs)


def test_scan_execution_benchmark_runs_all_boundaries_with_waveforms(
    tmp_path: Path,
) -> None:
    output = tmp_path / "results.jsonl"

    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            "-m",
            "benchmarks",
            "run",
            "scan-execution",
            "--points",
            "3",
            "--profile",
            "waveform",
            "--waveform-samples",
            "128",
            "--qubit-counts",
            "1,2,4",
            "--live-waveform",
            "--runners",
            "adhoc,scopecat-core,scopecat",
            "--repetitions",
            "1",
            "--warmups",
            "0",
            "--host-label",
            "test",
            "--output",
            str(output),
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    results = tuple(
        cast("dict[str, object]", json.loads(line))
        for line in output.read_text(encoding="utf-8").splitlines()
    )
    streamed_results = tuple(
        cast(
            "dict[str, object]",
            json.loads(line.removeprefix("BENCHMARK_RESULT=")),
        )
        for line in completed.stdout.splitlines()
        if line.startswith("BENCHMARK_RESULT=")
    )
    assert streamed_results == results
    by_case = {
        (
            result["runner"],
            cast("dict[str, object]", result["scenario"])["qubit_count"],
        ): result
        for result in results
    }
    assert set(by_case) == {
        (runner, qubit_count)
        for runner in ("adhoc", "scopecat-core", "scopecat")
        for qubit_count in (1, 2, 4)
    }
    assert all(result["points_completed"] == 3 for result in results)
    assert all(result["schema"] == "scopecat.benchmark_result.v1" for result in results)
    assert all(result["case_id"] == "scan-execution" for result in results)
    assert all(result["case_version"] == 9 for result in results)
    assert all(result["kind"] == "e2e" for result in results)
    assert all(
        cast("dict[str, object]", result["scenario"])["acquisition_dsp_policy"]
        == "prefer_device"
        for result in results
    )
    for qubit_count in (1, 2, 4):
        expected_retained_bytes = (2 * qubit_count + 2) * 128 * 8
        expected_total_bytes = 3 * expected_retained_bytes
        assert by_case[("adhoc", qubit_count)]["trigger_count"] == 3
        assert by_case[("scopecat-core", qubit_count)]["trigger_count"] == 2
        assert by_case[("scopecat", qubit_count)]["trigger_count"] == 2
        assert all(
            by_case[(runner, qubit_count)]["waveform_bytes_uploaded"]
            == expected_total_bytes
            for runner in ("adhoc", "scopecat-core", "scopecat")
        )
        assert all(
            by_case[(runner, qubit_count)]["live_waveform_bytes_retained"]
            == expected_retained_bytes
            for runner in ("adhoc", "scopecat-core", "scopecat")
        )
        assert (
            by_case[("adhoc", qubit_count)]["max_waveform_batch_bytes"]
            == expected_retained_bytes
        )
        scopecat_batch_bytes = cast(
            "int", by_case[("scopecat", qubit_count)]["max_waveform_batch_bytes"]
        )
        assert expected_retained_bytes <= scopecat_batch_bytes
        assert scopecat_batch_bytes <= 2 * expected_retained_bytes


def test_scopecat_benchmark_batches_measurement_appends(tmp_path: Path) -> None:
    work_dir = tmp_path / "scopecat-worker"

    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            "-m",
            "benchmarks",
            "run",
            "scan-execution",
            "--worker",
            "scopecat",
            "--point-count",
            "257",
            "--qubit-count",
            "2",
            "--profile",
            "waveform",
            "--acquisition-dsp",
            "target",
            "--waveform-samples",
            "128",
            "--host-label",
            "test",
            "--work-dir",
            str(work_dir),
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    database = next(work_dir.rglob("control.sqlite3"))
    with sqlite3.connect(database) as connection:
        append_ranges = cast(
            "list[tuple[int, int]]",
            connection.execute(
                """
                SELECT acquisition_start, record_count
                FROM execution_measurement_appends
                ORDER BY acquisition_start
                """
            ).fetchall(),
        )
    assert len(append_ranges) > 1
    next_start = 0
    for acquisition_start, record_count in append_ranges:
        assert acquisition_start == next_start
        assert 0 < record_count <= 256
        next_start += record_count
    assert next_start == 257
    object_root = work_dir / ".scopecat" / "objects"
    object_bytes = sum(
        path.stat().st_size for path in object_root.rglob("*") if path.is_file()
    )
    total_waveform_bytes = 257 * 6 * 128 * 8
    assert object_bytes < total_waveform_bytes // 2
    result_line = next(
        line
        for line in completed.stdout.splitlines()
        if line.startswith("BENCHMARK_RESULT=")
    )
    result = cast(
        "dict[str, object]",
        json.loads(result_line.removeprefix("BENCHMARK_RESULT=")),
    )
    scenario = cast("dict[str, object]", result["scenario"])
    assert scenario["acquisition_dsp_policy"] == "target"
    assert result["payload_spool_bytes_at_finish"] == 0
    peak_spool_bytes = cast("int", result["peak_payload_spool_bytes"])
    max_batch_bytes = cast("int", result["max_waveform_batch_bytes"])
    assert peak_spool_bytes > 0
    assert peak_spool_bytes <= 2 * max_batch_bytes


def test_target_dsp_rejects_the_unmatched_adhoc_runner(tmp_path: Path) -> None:
    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            "-m",
            "benchmarks",
            "run",
            "scan-execution",
            "--points",
            "1",
            "--profile",
            "waveform",
            "--acquisition-dsp",
            "target",
            "--runners",
            "adhoc,scopecat",
            "--repetitions",
            "1",
            "--warmups",
            "0",
            "--output",
            str(tmp_path / "results.jsonl"),
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "ad hoc runner does not model raw-trace" in completed.stderr


def test_result_retention_profile_separates_selected_data_from_control(
    tmp_path: Path,
) -> None:
    discarded = _result_worker(
        tmp_path,
        runner="adhoc",
        retention="discard",
        shots=8,
    )
    summary_small = _result_worker(
        tmp_path,
        runner="scopecat",
        retention="summary",
        shots=8,
    )
    summary_large = _result_worker(
        tmp_path,
        runner="scopecat",
        retention="summary",
        shots=64,
    )
    iq_and_bits = _result_worker(
        tmp_path,
        runner="scopecat",
        retention="iq-and-bits",
        shots=64,
    )

    assert discarded["selected_result_bytes"] == 0
    assert discarded["measurement_dataset_bytes"] == 0
    assert discarded["durable_bytes"] == 0
    assert summary_small["acquired_result_bytes"] == 512
    assert summary_large["acquired_result_bytes"] == 4096
    assert summary_small["selected_result_bytes"] == 32
    assert summary_large["selected_result_bytes"] == 32
    assert cast("int", summary_large["measurement_dataset_bytes"]) < (
        cast("int", summary_small["measurement_dataset_bytes"]) + 1024
    )

    assert iq_and_bits["selected_result_bytes"] == 4128
    assert cast("int", iq_and_bits["measurement_dataset_bytes"]) > cast(
        "int", summary_large["measurement_dataset_bytes"]
    )
    for result in (summary_small, summary_large, iq_and_bits):
        assert result["points_completed"] == 2
        assert result["durable_bytes"] == (
            cast("int", result["measurement_dataset_bytes"])
            + cast("int", result["control_and_provenance_bytes"])
        )


@pytest.mark.parametrize(
    ("qubit_count", "retention"),
    [(16, "summary"), (64, "bit-shots"), (128, "iq-and-bits")],
)
def test_scaled_result_retention_prepares_all_required_hardware_state(
    tmp_path: Path,
    qubit_count: int,
    retention: str,
) -> None:
    result = _result_worker(
        tmp_path,
        runner="scopecat",
        retention=retention,
        shots=4,
        qubit_count=qubit_count,
    )
    assert result["points_completed"] == 2
    assert cast("int", result["selected_result_bytes"]) > 0


def _result_worker(
    tmp_path: Path,
    *,
    runner: str,
    retention: str,
    shots: int,
    qubit_count: int = 2,
) -> dict[str, object]:
    work_dir = tmp_path / f"{runner}-{retention}-{shots}"
    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            "-m",
            "benchmarks",
            "run",
            "scan-execution",
            "--worker",
            runner,
            "--point-count",
            "2",
            "--qubit-count",
            str(qubit_count),
            "--profile",
            "results",
            "--retention",
            retention,
            "--shots",
            str(shots),
            "--waveform-samples",
            "72",
            "--host-label",
            "test",
            "--work-dir",
            str(work_dir),
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
    return cast(
        "dict[str, object]",
        json.loads(result_line.removeprefix("BENCHMARK_RESULT=")),
    )
