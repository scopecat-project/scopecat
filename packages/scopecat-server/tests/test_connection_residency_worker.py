"""The shared adapter contract across real worker and daemon ownership boundaries."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

import httpx2
import psutil
import pytest
from fastapi.testclient import TestClient
from scopecat.api.lab import LabClient
from scopecat.daemon.client import DaemonClient, DaemonClientError, DaemonConflictError
from scopecat.kernel.errors import RunFailed, RunFinalizationFailed
from scopecat.kernel.state import StateValue
from scopecat.planning.system import ExperimentSystem
from scopecat.records.config import instrument_bindings
from scopecat.records.costs import RunMeasuredCosts
from scopecat.records.run import RunSnapshot
from scopecat.sdk.instruments.backend import (
    BackendInvokeRequest,
    BackendOperationArgument,
)
from scopecat_testkit.connection_residency import (
    ResidencyProbe,
    VolatileProgramTarget,
    check_connection_residency,
    residency_config,
    residency_experiment,
    volatile_backend,
)

from scopecat_server.instruments.backend import (
    InstrumentBackendEndpoint,
    InstrumentHandleInvalid,
    LocalInstrumentBackendEndpoint,
)
from scopecat_server.instruments.worker import SubprocessInstrumentBackendEndpoint
from scopecat_server.runtime import LocalDaemonRuntime
from scopecat_server.storage.sqlite.execution import SQLiteMeasurementDatasetRepository


def _worker(project: Path) -> SubprocessInstrumentBackendEndpoint:
    source = project / "src"
    source.mkdir(parents=True, exist_ok=True)
    (source / "residency_worker.py").write_text(
        "from scopecat_testkit.connection_residency import (\n"
        "    volatile_backend as create_backend,\n"
        ")\n",
        encoding="utf-8",
    )
    return SubprocessInstrumentBackendEndpoint(
        project, "residency_worker:create_backend"
    )


@contextmanager
def _lab(
    project: Path,
    endpoint: InstrumentBackendEndpoint,
    target: VolatileProgramTarget,
) -> Generator[tuple[LabClient, DaemonClient]]:
    config = residency_config()
    with (
        LocalDaemonRuntime(
            project, bootstrap_config=config, instrument_endpoint=endpoint
        ) as runtime,
        TestClient(runtime.app()) as transport,
    ):

        def send(request: httpx2.Request) -> httpx2.Response:
            response = transport.request(
                request.method,
                request.url.raw_path.decode(),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx2.Response(
                response.status_code,
                content=response.content,
                headers=dict(response.headers),
            )

        with (
            DaemonClient(
                "http://testserver", transport=httpx2.MockTransport(send)
            ) as client,
            LabClient(
                client,
                build_experiment_system=lambda _config, catalog: ExperimentSystem(
                    instrument_catalog=catalog, domain_compiler=target
                ),
            ) as lab,
        ):
            yield lab, client


def _assert_fenced_unknown(
    run: Callable[[], RunSnapshot], client: DaemonClient, problem_code: str | None
) -> None:
    previous = {item.run_id for item in client.list_runs().items}
    with pytest.raises(RunFinalizationFailed) as failed:
        run()
    assert failed.value.terminal_persistence == "unconfirmed"
    assert isinstance(failed.value.__cause__, DaemonConflictError)
    assert failed.value.finalization_problems[-1].code == "run_terminal_commit_failed"
    if problem_code is not None:
        assert failed.value.problems[0].code == problem_code
    [run_id] = [
        item.run_id for item in client.list_runs().items if item.run_id not in previous
    ]
    measured = client.get_run_measured_costs(run_id)
    assert measured.compilation is None  # Terminal persistence is unconfirmed.
    assert (
        measured.operations
    )  # Earlier costs remain readable before a terminal record.
    if problem_code is not None:
        assert sum(item.status == "unknown" for item in measured.operations) == 1
    detail = client.get_run(run_id)
    assert detail.control.state == "attention_required"
    assert detail.control.completed_point_count == 0
    assert any(item.status == "quarantined" for item in detail.resources)
    [segment] = client.get_run_execution_segments(run_id).items
    assert segment.result == "interrupted"
    assert segment.certainty == "indeterminate"
    assert segment.reason == detail.control.attention_reason
    if problem_code is None:
        assert segment.reason == "run_instrument_acquisition_prepare_unknown"
        return
    unknown_events = [
        event
        for event in client.replay_events(run_id=run_id).items
        if event.kind == "run_hardware_batch_unknown"
    ]
    assert len(unknown_events) == 1
    codes = unknown_events[0].payload["problem_codes"]
    assert isinstance(codes, list)
    assert problem_code in codes


def test_shared_contract_through_real_worker_and_daemon(tmp_path: Path) -> None:
    probe = ResidencyProbe(tmp_path)
    endpoint = _worker(tmp_path)
    with _lab(tmp_path, endpoint, VolatileProgramTarget()) as (lab, client):

        def run(points: int) -> RunSnapshot:
            return lab.run(residency_experiment(points)).snapshot

        check_connection_residency(
            run,
            probe,
            assert_unknown=lambda execute: _assert_fenced_unknown(
                execute, client, "fixture_trigger_response_lost"
            ),
        )
    # Releasing run ownership can retain the daemon connection. Each fresh run
    # still establishes its own setup; shutdown/replacement is checked separately.
    connections = [event for event in probe.events() if event.operation == "connect"]
    assert len({event.connection for event in connections}) == len(connections) == 1
    assert probe.counts() == (5, 5, 4)


def test_replacement_worker_reloads_equal_content_before_next_acquisition(
    tmp_path: Path,
) -> None:
    target = VolatileProgramTarget()
    probe = ResidencyProbe(tmp_path)
    first = _worker(tmp_path)
    with _lab(tmp_path, first, target) as (lab, _client):
        assert lab.run(residency_experiment(1)).status == "completed"
    assert probe.counts() == (1, 1, 1)
    second = _worker(tmp_path)
    assert second.worker_pid != first.worker_pid
    # Reuse target code/content and the original durable project; neither is
    # evidence that a replacement worker has retained its predecessor's program.
    with _lab(tmp_path, second, target) as (lab, _client):
        assert lab.run(residency_experiment(2)).status == "completed"
    assert probe.counts() == (2, 3, 3)
    connections = [
        event.connection for event in probe.events() if event.operation == "connect"
    ]
    assert len(set(connections)) == 2
    for connection in connections:
        operations = [
            event.operation
            for event in probe.events()
            if event.connection == connection
        ]
        assert operations[:3] == ["connect", "setup", "trigger"]
        first_setup = next(
            event
            for event in probe.events()
            if event.connection == connection and event.operation == "setup"
        )
        assert first_setup.content is None
        assert operations[-1] == "disconnect"


def test_dead_worker_after_setup_never_repeats_or_transfers_trigger(
    tmp_path: Path,
) -> None:
    endpoint = _worker(tmp_path)
    probe = ResidencyProbe(tmp_path)

    def lose_worker() -> None:
        worker = psutil.Process(endpoint.worker_pid)
        worker.kill()
        worker.wait(timeout=5)

    target = VolatileProgramTarget(before_trigger=lose_worker)
    with _lab(tmp_path, endpoint, target) as (lab, client):
        _assert_fenced_unknown(
            lambda: lab.run(residency_experiment(2)).snapshot,
            client,
            None,
        )
        assert probe.counts() == (1, 0, 0)
    assert probe.counts() == (1, 0, 0)


def test_worker_generation_rejects_predecessors_loaded_handle(tmp_path: Path) -> None:
    first = _worker(tmp_path)
    [binding] = instrument_bindings(residency_config())
    [description] = first.describe((binding,)).instruments
    connection = first.connect(binding=binding, expected=description)
    try:
        receipt = first.invoke(
            connection.handle,
            BackendInvokeRequest(
                interface_id="testkit.volatile_program/v1",
                operation_id="load",
                arguments=(
                    BackendOperationArgument(id="content", value=StateValue("program")),
                ),
            ),
        )
        assert receipt.status == "invoked"
    finally:
        first.shutdown()
    second = _worker(tmp_path)
    try:
        with pytest.raises(InstrumentHandleInvalid, match="generation"):
            second.invoke(
                connection.handle,
                BackendInvokeRequest(
                    interface_id="testkit.volatile_program/v1", operation_id="trigger"
                ),
            )
        assert ResidencyProbe(tmp_path).counts() == (1, 0, 0)
    finally:
        second.shutdown()


@pytest.mark.parametrize("raised", [False, True])
def test_acquisition_failure_precedes_cleanup_with_saved_diagnostic_links(
    tmp_path: Path,
    raised: bool,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    if raised:
        (tmp_path / "raise-acquisition").touch()
    (source / "dual_failure.py").write_text(
        """
import os
from scopecat.sdk.instruments import (
    DriverRejected, DriverCatalog, InstrumentBackend, DriverFault,
)
from scopecat.sdk.problems import ProblemPhase, problem
from scopecat_testkit.connection_residency import (
    VolatileProgramDriver, VolatileProgramProvider, ResidencyProbe,
)

class Driver(VolatileProgramDriver):
    def collect(self, request):
        self.probe.record(self.connection, "collect", self.loaded)
        print("native detector acquiring", flush=True)
        os.write(2, bytes([100, 255, 10]))
        failure = problem(
            "fixture_acquisition_failed", "Detector acquisition failed",
            phase=ProblemPhase.EXECUTION,
        )
        if (self.probe.root / "raise-acquisition").exists():
            raise DriverFault(failure)
        return DriverRejected((failure,))
    def abort(self):
        self.probe.record(self.connection, "abort", self.loaded)
        raise RuntimeError("vendor abort link failure " * 1000)

class Provider(VolatileProgramProvider):
    def connect(self, context):
        return Driver(self.probe, context.binding.id)

def create_backend(root):
    provider = Provider(ResidencyProbe(root))
    return InstrumentBackend(
        provider=provider,
        driver_catalog=DriverCatalog(provider_id=provider.provider_id),
    )
""",
        encoding="utf-8",
    )
    endpoint = SubprocessInstrumentBackendEndpoint(
        tmp_path, "dual_failure:create_backend"
    )
    with _lab(tmp_path, endpoint, VolatileProgramTarget()) as (lab, client):
        with pytest.raises(RunFinalizationFailed) as failed:
            lab.run(residency_experiment(2))
        assert failed.value.problems[0].code == "fixture_acquisition_failed"
        assert failed.value.terminal_persistence == "unconfirmed"
        [saved] = client.list_runs().items
        assert saved.control.state == "attention_required"
        evidence = client.get_run_failure_evidence(saved.run_id)
        assert evidence.primary is not None
        assert evidence.primary.code == "fixture_acquisition_failed"
        assert evidence.terminal_persistence == "unconfirmed"
        assert "run_instrument_abort_unknown" in [
            item.code for item in evidence.secondary
        ]
        assert len(evidence.diagnostics) == 2
        assert {item.operation for item in evidence.diagnostics} == {"collect", "abort"}
        assert all(
            item.href and item.instrument_id == "source-0"
            for item in evidence.diagnostics
        )
        display = client.get_worker_diagnostics(evidence.diagnostics[0].generation)
        assert b"vendor abort link failure" in display
        assert b"native detector acquiring" in display
        assert "\ufffd" in display.decode("utf-8")
        raw = client.get_worker_diagnostics(
            evidence.diagnostics[0].generation, raw=True
        )
        assert len(raw) <= 256 * 1024
        assert b"vendor abort link failure" not in evidence.model_dump_json().encode()
        for invalid in ("not-a-generation", "../config", "a" * 33):
            with pytest.raises((DaemonClientError, httpx2.HTTPStatusError)) as rejected:
                client.get_worker_diagnostics(invalid)
            assert rejected.value.response.status_code in (404, 422)
        with pytest.raises((DaemonClientError, httpx2.HTTPStatusError)) as absent:
            client.get_worker_diagnostics("0" * 32)
        assert absent.value.response.status_code == 410
        assert ResidencyProbe(tmp_path).counts() == (1, 1, 1)
    assert ResidencyProbe(tmp_path).counts() == (1, 1, 1)


def test_measured_costs_compare_cold_warm_and_explicit_reconnect(
    tmp_path: Path,
) -> None:
    from scopecat.daemon.wire import InstrumentReleaseCommand

    target = VolatileProgramTarget()
    probe = ResidencyProbe(tmp_path)
    endpoint = _worker(tmp_path)
    with _lab(tmp_path, endpoint, target) as (lab, client):
        summaries: list[RunMeasuredCosts] = []
        for index in range(3):
            if index == 2:
                [binding] = instrument_bindings(residency_config())
                client.release_instruments(
                    InstrumentReleaseCommand(instrument_ids=(binding.id,))
                )
            result = lab.run(residency_experiment(2))
            assert result.status == "completed"
            summary = client.get_run_measured_costs(result.snapshot.run_id)
            summaries.append(summary)
            assert summary.compilation is not None
            assert summary.compilation.seconds >= 0
            assert summary.compilation.lazy_compilation_seconds is None
            assert len(summary.finalizations) == 1
            assert summary.finalizations[0].seconds >= 0
            assert summary.terminal_commit_seconds is None
            assert not summary.truncated
            assert (
                len(summary.operations) == 7
            )  # One setup plus prepare/trigger/collect per point.
            measured = [
                item.measured
                for item in summary.operations
                if item.measured is not None
            ]
            size = len(target.content.encode("utf-8"))
            assert sum(item.uploaded_bytes or 0 for item in measured) == size
            assert sum(item.reused_bytes or 0 for item in measured) == size * 2
            assert {item.retained_bytes for item in measured} == {size}
            assert all(item.rendered_bytes is None for item in measured)
            assert (
                len([item for item in measured if item.transfer_seconds is not None])
                == 1
            )
            assert (
                len([item for item in measured if item.acquire_seconds is not None])
                == 2
            )
        contexts = [
            {item.connection_context for item in summary.operations}
            for summary in summaries
        ]
        assert contexts == [{"cold"}, {"warm"}, {"reconnect"}]
        generations = [
            {item.connection_generation for item in summary.operations}
            for summary in summaries
        ]
        assert len(generations[0]) == 1
        assert generations[0] == generations[1]
        assert generations[1].isdisjoint(generations[2])
        assert probe.counts() == (3, 6, 6)


@pytest.mark.parametrize("fault", ["trigger_unknown", "collect_unknown"])
@pytest.mark.parametrize("completed", [1, 2])
def test_default_buffered_measurements_survive_late_unknown_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    completed: int,
) -> None:
    from typing import Literal, cast

    import scopecat.daemon.execution as execution

    # Hold only the transport clock constant: default record/byte budgets remain
    # untouched, and the second completed point stays in the client tail.
    monkeypatch.setattr(execution, "monotonic", lambda: 0.0)
    probe = ResidencyProbe(tmp_path)
    calls = 0
    armed = False

    def inject_late_fault() -> None:
        nonlocal calls
        if not armed:
            return
        calls += 1
        if calls == completed + 1:
            probe.inject(cast('Literal["trigger_unknown", "collect_unknown"]', fault))

    target = VolatileProgramTarget(before_trigger=inject_late_fault)
    with _lab(
        tmp_path, LocalInstrumentBackendEndpoint(volatile_backend(tmp_path)), target
    ) as (lab, client):
        baseline = lab.run(residency_experiment(completed))
        baseline_preview = client.measurement_preview(baseline.id, limit=completed)
        expected = baseline_preview.items
        before = probe.counts()
        armed = True
        previous = {item.run_id for item in client.list_runs().items}
        with pytest.raises(RunFinalizationFailed) as failure:
            lab.run(residency_experiment(completed + 1))
        assert failure.value.execution_outcome.certainty == "indeterminate"
        [run_id] = [
            item.run_id
            for item in client.list_runs().items
            if item.run_id not in previous
        ]
        detail = client.get_run(run_id)
        assert detail.control.state == "attention_required"
        assert any(item.status == "quarantined" for item in detail.resources)
        retained_preview = client.measurement_preview(run_id, limit=completed + 1)
        actual = retained_preview.items
        assert len(actual) == completed
        assert baseline_preview.dataset_schema is not None
        assert retained_preview.dataset_schema is not None
        assert (
            retained_preview.dataset_schema.variables
            == baseline_preview.dataset_schema.variables
        )
        assert [
            (row.point_index, row.coordinates, row.observables) for row in actual
        ] == [(row.point_index, row.coordinates, row.observables) for row in expected]
        assert detail.control.completed_point_count == 1
        [safe_group] = client.get_run_recovery_groups(run_id).items
        assert safe_group.completion.point_indices == (0,)
        assert safe_group.completion.output_kind == "measurement"
        [segment] = client.get_run_execution_segments(run_id).items
        assert segment.end_point_count == 1
        assert probe.counts()[1] - before[1] == completed + 1
        assert probe.counts()[2] - before[2] == completed + (fault == "collect_unknown")
        counts = probe.counts()
    with _lab(
        tmp_path, LocalInstrumentBackendEndpoint(volatile_backend(tmp_path)), target
    ) as (_lab_again, client):
        assert client.measurement_preview(run_id, limit=completed + 1).items == actual
        assert client.get_run_coverage(run_id).completed_point_count == 1
        assert client.get_run_recovery_groups(run_id).items == (safe_group,)
        assert client.get_run(run_id).control.state == "attention_required"
        assert probe.counts() == counts


@pytest.mark.parametrize("during_unknown", [False, True])
def test_measurement_persistence_failure_cannot_publish_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    during_unknown: bool,
) -> None:
    import scopecat.daemon.execution as execution

    monkeypatch.setattr(execution, "monotonic", lambda: 0.0)
    probe = ResidencyProbe(tmp_path)
    calls = 0

    def fail_append(*_args: object) -> None:
        raise OSError("injected measurement storage failure")

    def inject_failure() -> None:
        nonlocal calls
        calls += 1
        if calls == (3 if during_unknown else 1):
            monkeypatch.setattr(
                SQLiteMeasurementDatasetRepository, "prepare_append", fail_append
            )
            if during_unknown:
                probe.inject("trigger_unknown")

    target = VolatileProgramTarget(before_trigger=inject_failure)
    with _lab(
        tmp_path, LocalInstrumentBackendEndpoint(volatile_backend(tmp_path)), target
    ) as (lab, client):
        with pytest.raises(RunFinalizationFailed if during_unknown else RunFailed):
            lab.run(residency_experiment(3))
        [run] = client.list_runs().items
        detail = client.get_run(run.run_id)
        expected = 1 if during_unknown else 0
        assert detail.control.completed_point_count == expected
        assert len(client.measurement_preview(run.run_id, limit=3).items) == expected
        assert probe.counts()[1:] == ((3, 2) if during_unknown else (1, 1))
        if during_unknown:
            assert detail.control.state == "attention_required"
            assert any(
                resource.status == "quarantined" for resource in detail.resources
            )
            evidence = client.get_run_failure_evidence(run.run_id)
            assert evidence.primary is not None
            assert evidence.primary.code == "fixture_trigger_response_lost"
            [retention] = [
                item
                for item in evidence.secondary
                if item.code == "run_measurement_retention_failed"
            ]
            assert retention.phase.value == "persistence"
