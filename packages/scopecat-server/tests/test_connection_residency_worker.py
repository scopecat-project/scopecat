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
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.kernel.state import StateValue
from scopecat.planning.system import ExperimentSystem
from scopecat.records.config import instrument_bindings
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
)

from scopecat_server.instruments.backend import InstrumentHandleInvalid
from scopecat_server.instruments.worker import SubprocessInstrumentBackendEndpoint
from scopecat_server.runtime import LocalDaemonRuntime


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
    endpoint: SubprocessInstrumentBackendEndpoint,
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
    # Current daemon finalization surfaces the revoked lease after preserving the
    # original hardware uncertainty. Qualify actual evidence, not a relabeled error.
    with pytest.raises(DaemonConflictError, match="lease is absent, stale, or expired"):
        run()
    [run_id] = [
        item.run_id for item in client.list_runs().items if item.run_id not in previous
    ]
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
