from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib.util import find_spec
from multiprocessing.process import BaseProcess
from pathlib import Path
from threading import Thread
from typing import Annotated, Protocol, cast

import httpx2
import psutil
import pytest
from fastapi.testclient import TestClient
from scopecat.api.lab import LabClient
from scopecat.authoring import (
    ExperimentContext,
    FloatType,
    Input,
    ModuleContext,
    ProductRef,
    ScalarType,
    coordinate,
    experiment,
    module,
)
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import DaemonHealth
from scopecat.daemon.wire import InstrumentSessionOpenCommand
from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.program.products import product_axis
from scopecat.records.config import ConfigProfileSnapshot, instrument_bindings
from scopecat.records.instrument import state_member_target
from scopecat.records.measurement import (
    InstrumentAcquisitionEvidence,
    MeasurementArray,
    MeasurementPointCloudPointDomain,
    MeasurementScalar,
    MeasurementUnavailable,
)
from scopecat.sdk.instruments import InterfaceRef
from scopecat.sdk.instruments.backend import (
    BackendApplyRequest,
    BackendCollectRequest,
    BackendCollectResult,
    BackendInvokeRequest,
    BackendOperationArgument,
    BackendPayload,
    BackendReadRequest,
    BackendStateMemberWrite,
)
from scopecat.sdk.instruments.commands import InvokeCommand
from scopecat.sdk.payloads import EncodedPayloadContent
from scopecat_testkit.instrument_drivers import load_config

from scopecat_server.errors import BackendConflict
from scopecat_server.instruments.backend import (
    InstrumentBackendError,
    InstrumentBackendRejected,
    InstrumentBackendUnavailable,
    InstrumentHandleInvalid,
)
from scopecat_server.instruments.worker import SubprocessInstrumentBackendEndpoint
from scopecat_server.runtime import LocalDaemonRuntime
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane

_FIXTURE = Path(__file__).parent / "fixtures" / "instrument_worker_project"
_BACKEND = "worker_fixture.backend:create_backend"

_GAIN = InterfaceRef("tests.control/v1").property("gain")


def _gain_read_request() -> BackendReadRequest:
    return BackendReadRequest(targets=(state_member_target(_GAIN),))


_RAGGED_CONTROL = InterfaceRef("tests.control/v1")
_RAGGED_GAIN_PROPERTY = _RAGGED_CONTROL.property("gain")
_RAGGED_TRACE_RESULT = _RAGGED_CONTROL.acquisition("sample").result("ragged_trace")
_RAGGED_GAIN = coordinate("gain", FloatType())


class _ArrowColumn(Protocol):
    def to_pylist(self) -> list[object]: ...


class _ArrowTable(Protocol):
    def __getitem__(self, name: str) -> _ArrowColumn: ...


class _ArrowRecordBatch(_ArrowTable, Protocol):
    @property
    def num_rows(self) -> int: ...


@module(id="tests.worker.ragged_capture")
def _ragged_capture(
    context: ModuleContext,
    gain: Annotated[Input[float], ScalarType(FloatType())],
) -> ProductRef:
    source = context._resource("source", requires=(_RAGGED_CONTROL,))
    context._bind_property(source, _RAGGED_GAIN_PROPERTY, value=gain)
    trace = context._product(
        "trace",
        unit="V",
        axes=(product_axis("sample", size=None),),
    )
    context._acquire(
        "capture",
        resource=source,
        results={_RAGGED_TRACE_RESULT: trace},
    )
    return trace


@experiment(id="tests.worker.ragged_point_cloud", kind="ragged_point_cloud")
def _ragged_point_cloud(experiment: ExperimentContext) -> None:
    experiment.points(
        (
            {_RAGGED_GAIN: 2.0},
            {_RAGGED_GAIN: 4.0},
            {_RAGGED_GAIN: 1.0},
        )
    )
    capture = experiment.use(_ragged_capture(gain=_RAGGED_GAIN))
    experiment.alias(capture, record_id="trace")


def test_spawned_worker_executes_closed_driver_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diagnostics = tmp_path / "startup"
    monkeypatch.setenv("SCOPECAT_STARTUP_DIAGNOSTICS", str(diagnostics))
    project = _copy_project(tmp_path)
    assert "worker_fixture.backend" not in sys.modules
    endpoint = SubprocessInstrumentBackendEndpoint(project, _BACKEND)
    worker_process = psutil.Process(endpoint.worker_pid)
    config = load_config()

    assert "worker_fixture.backend" not in sys.modules
    assert endpoint.healthy
    assert endpoint.worker_pid != os.getpid()
    assert int((project / "worker.pid").read_text(encoding="utf-8")) == (
        endpoint.worker_pid
    )
    [binding] = instrument_bindings(config)
    [expected] = endpoint.describe((binding,)).instruments
    assert endpoint.provider_id == "tests.spawned_provider"
    assert endpoint.driver_catalog.provider_id == endpoint.provider_id
    assert endpoint.payload_catalog.codecs[0].schema_id == "pulse_program"
    describe_context = json.loads(
        (project / "describe-context.json").read_text(encoding="utf-8")
    )
    assert describe_context == {
        "bindings": [binding.model_dump(mode="json")],
    }
    assert set(describe_context["bindings"][0]) == {
        "id",
        "driver_id",
        "connection",
    }
    assert endpoint.probe(binding) == expected
    assert (project / "driver-events.log").read_text(encoding="utf-8") == (
        "disconnect:source-0\n"
    )

    connection = endpoint.connect(
        binding=binding,
        expected=expected,
    )
    connect_context = json.loads(
        (project / "connect-context.json").read_text(encoding="utf-8")
    )
    assert connect_context == {
        "binding": binding.model_dump(mode="json"),
    }
    assert set(connect_context["binding"]) == {
        "id",
        "driver_id",
        "connection",
    }
    assert not hasattr(connection, "driver")
    endpoint.apply_state(
        connection.handle,
        BackendApplyRequest(
            assignments=(
                BackendStateMemberWrite(
                    target=state_member_target(_GAIN),
                    value=StateValue(2.5),
                ),
            )
        ),
    )
    state = endpoint.read_state(connection.handle, _gain_read_request())
    [observation] = state.observations
    assert observation.metadata["worker_pid"] == endpoint.worker_pid
    assert observation.value == StateValue(2.5)

    content = b"\x00\xffprogram\x00"
    invoke = endpoint.invoke(
        connection.handle,
        BackendInvokeRequest(
            interface_id="tests.control/v1",
            operation_id="play",
            arguments=(
                BackendOperationArgument(
                    id="program",
                    value=StateValue(PayloadRef(payload_id="program")),
                ),
            ),
            payloads={
                "program": BackendPayload(
                    id="program",
                    schema_id="pulse_program",
                    codec_id="tests.raw",
                    codec_version=1,
                    media_type="application/octet-stream",
                    content_format="bytes",
                    content=EncodedPayloadContent.from_bytes(content),
                )
            },
        ),
    )
    assert invoke.metadata == {
        "payload_hex": content.hex(),
        "payload_types": ["_DecodedProgram"],
        "payload_hashes": [EncodedPayloadContent.from_bytes(content).content_hash()],
        "worker_pid": endpoint.worker_pid,
    }
    assert "worker_fixture.backend" not in sys.modules

    collected = endpoint.collect(
        connection.handle,
        BackendCollectRequest(
            interface_id="tests.control/v1",
            acquisition_id="sample",
            results=(BackendCollectResult(request_id="signal", result_id="signal"),),
        ),
    )
    assert collected.readback is not None
    assert collected.readback.values["signal"] == MeasurementScalar.create(
        dtype="float64",
        value=1.25,
        unit="ratio",
    )

    endpoint.disconnect(connection.handle)
    assert (project / "driver-events.log").read_text(encoding="utf-8") == (
        "disconnect:source-0\ndisconnect:source-0\n"
    )
    with pytest.raises(InstrumentHandleInvalid, match="stale"):
        endpoint.read_state(connection.handle, _gain_read_request())

    replacement = endpoint.connect(
        binding=binding,
        expected=expected,
    )
    assert replacement.handle.endpoint_id == connection.handle.endpoint_id
    assert replacement.handle.token != connection.handle.token
    endpoint.abort(replacement.handle)
    endpoint.shutdown()
    endpoint.shutdown()
    assert (project / "driver-events.log").read_text(encoding="utf-8") == (
        "disconnect:source-0\ndisconnect:source-0\nabort\ndisconnect:source-0\n"
    )
    assert not endpoint.healthy
    assert not worker_process.is_running()
    [trace] = diagnostics.glob("instrument-startup-*.log")
    evidence = trace.read_text(encoding="utf-8")
    assert f"pid={endpoint.worker_pid}" in evidence
    assert "backend constructed; describing catalogs" in evidence
    assert "readiness sent" in evidence


def test_ragged_point_cloud_run_survives_daemon_and_worker_boundaries(
    tmp_path: Path,
) -> None:
    project = _copy_project(tmp_path)
    config = _ragged_point_cloud_config()

    with (
        LocalDaemonRuntime(
            project,
            bootstrap_config=config,
            instrument_backend_spec=_BACKEND,
        ) as runtime,
        TestClient(runtime.app()) as transport,
        _http_daemon_client(transport) as daemon,
    ):
        with LabClient(daemon) as lab:
            preview = lab.preview(_ragged_point_cloud)
            run = lab.run(_ragged_point_cloud)

        assert preview.point_count == 3
        assert preview.schema is not None
        assert preview.schema.dimensions[1].size is None
        assert run.status == "completed"
        run_id = run.id
        worker_pid = int((project / "worker.pid").read_text(encoding="utf-8"))
        assert worker_pid != os.getpid()

    with (
        LocalDaemonRuntime(
            project,
            bootstrap_config=config,
            instrument_backend_spec=_BACKEND,
        ) as reopened,
        TestClient(reopened.app()) as transport,
        _http_daemon_client(transport) as daemon,
        LabClient(daemon) as lab,
    ):
        persisted = lab.get_run(run_id)
        persisted_status = persisted.status
        dataset = persisted.measurements()
        assert len(dataset) == 3
        batches = list(
            cast(
                "Iterator[_ArrowRecordBatch]",
                persisted.measurements().project().to_record_batch_reader(batch_size=2),
            )
        )

    assert persisted_status == "completed"
    point_domain = dataset.schema.point_domain
    assert isinstance(point_domain, MeasurementPointCloudPointDomain)
    assert [column.id for column in point_domain.columns] == ["gain"]
    assert dataset.coords["gain"].values == (2.0, 4.0, 1.0)
    point_dimension, sample_dimension = dataset.data_vars["trace"].dims
    assert point_dimension == "point"
    assert dataset.dims == {"point": 3, sample_dimension: None}
    assert [
        value.shape
        for value in dataset.data_vars["trace"].raw_values
        if isinstance(value, MeasurementArray)
    ] == [(2,), (4,), (1,)]
    assert [batch.num_rows for batch in batches] == [2, 1]
    assert [
        point for batch in batches for point in batch["point_index"].to_pylist()
    ] == [0, 1, 2]
    trace_evidence = tuple(
        record.acquisition_evidence.for_variable("trace") for record in dataset.records
    )
    assert all(
        isinstance(evidence, InstrumentAcquisitionEvidence)
        and evidence.instrument_id == "source-0"
        for evidence in trace_evidence
    )

    if find_spec("pyarrow") is not None:
        table = cast("_ArrowTable", dataset.project().to_arrow())
        assert table["trace"].to_pylist() == [
            [2.0, 2.1],
            [4.0, 4.1, 4.2, 4.3],
            [1.0],
        ]


def test_worker_rejects_changed_contract_and_foreign_generation(
    tmp_path: Path,
) -> None:
    first_project = _copy_project(tmp_path / "first")
    second_project = _copy_project(tmp_path / "second")
    first = SubprocessInstrumentBackendEndpoint(first_project, _BACKEND)
    second = SubprocessInstrumentBackendEndpoint(second_project, _BACKEND)
    config = load_config()
    [binding] = instrument_bindings(config)
    [expected] = first.describe((binding,)).instruments
    connection = first.connect(
        binding=binding,
        expected=expected,
    )

    with pytest.raises(InstrumentHandleInvalid, match="generation"):
        second.read_state(connection.handle, _gain_read_request())
    with pytest.raises(InstrumentBackendRejected) as rejected:
        first.connect(
            binding=binding,
            expected=expected.model_copy(update={"implementation_version": "changed"}),
        )
    assert [item.code for item in rejected.value.problems] == [
        "instrument_description_changed"
    ]

    first.shutdown()
    second.shutdown()


def test_worker_crash_fails_requests_and_marks_the_endpoint_unhealthy(
    tmp_path: Path,
) -> None:
    project = _copy_project(tmp_path)
    endpoint = SubprocessInstrumentBackendEndpoint(project, _BACKEND)
    config = load_config()
    bindings = instrument_bindings(config)
    worker_pid = endpoint.worker_pid
    psutil.Process(worker_pid).kill()

    with pytest.raises(InstrumentBackendUnavailable, match="unavailable"):
        endpoint.describe(bindings)
    assert not endpoint.healthy

    endpoint.shutdown()


def test_runtime_releases_project_lock_after_worker_start_failure(
    tmp_path: Path,
) -> None:
    project = _copy_project(tmp_path)

    with pytest.raises(InstrumentBackendUnavailable, match="failed to start"):
        LocalDaemonRuntime(
            project,
            instrument_backend_spec="worker_fixture.backend:create_failing_backend",
        )

    with LocalDaemonRuntime(project) as reopened:
        assert reopened.application.health().status == "ok"


def test_worker_crash_degrades_runtime_health(tmp_path: Path) -> None:
    project = _copy_project(tmp_path)
    with LocalDaemonRuntime(
        project,
        bootstrap_config=load_config(),
        instrument_backend_spec=_BACKEND,
    ) as runtime:
        worker_pid = int((project / "worker.pid").read_text(encoding="utf-8"))
        psutil.Process(worker_pid).kill()
        client = TestClient(runtime.app())
        deadline = time.monotonic() + 2
        while (
            health := DaemonHealth.model_validate_json(
                client.get("/api/v1/health").content
            )
        ).status != "degraded":
            if time.monotonic() >= deadline:
                pytest.fail("runtime health did not observe worker failure")
            time.sleep(0.01)

    assert health.status == "degraded"


def test_worker_rejects_invalid_collect_array_without_poisoning_protocol(
    tmp_path: Path,
) -> None:
    project = _copy_project(tmp_path)
    endpoint = SubprocessInstrumentBackendEndpoint(project, _BACKEND)
    config = load_config()
    [binding] = instrument_bindings(config)
    [expected] = endpoint.describe((binding,)).instruments
    connection = endpoint.connect(
        binding=binding,
        expected=expected,
    )

    valid = endpoint.collect(
        connection.handle,
        BackendCollectRequest(
            interface_id="tests.control/v1",
            acquisition_id="sample",
            results=(
                BackendCollectResult(
                    request_id="signal",
                    result_id="complex_array",
                ),
            ),
        ),
    )
    assert valid.readback is not None
    assert valid.readback.values["signal"] == MeasurementArray.create(
        dtype="complex128",
        unit="ratio",
        values=(complex(1.0, -0.5),),
    )

    with pytest.raises(InstrumentBackendError, match="request failed"):
        endpoint.collect(
            connection.handle,
            BackendCollectRequest(
                interface_id="tests.control/v1",
                acquisition_id="sample",
                results=(
                    BackendCollectResult(
                        request_id="signal",
                        result_id="invalid_array",
                    ),
                ),
            ),
        )
    assert endpoint.healthy
    assert (
        endpoint.read_state(connection.handle, _gain_read_request()).instrument_id
        == "source-0"
    )
    endpoint.shutdown()


def test_worker_round_trips_unavailable_collect_values(tmp_path: Path) -> None:
    project = _copy_project(tmp_path)
    endpoint = SubprocessInstrumentBackendEndpoint(project, _BACKEND)
    config = load_config()
    [binding] = instrument_bindings(config)
    [expected] = endpoint.describe((binding,)).instruments
    connection = endpoint.connect(
        binding=binding,
        expected=expected,
    )

    receipt = endpoint.collect(
        connection.handle,
        BackendCollectRequest(
            interface_id="tests.control/v1",
            acquisition_id="sample",
            results=(
                BackendCollectResult(
                    request_id="signal",
                    result_id="unavailable",
                ),
            ),
        ),
    )

    assert receipt.readback is not None
    assert receipt.readback.values["signal"] == MeasurementUnavailable.create(
        dtype="float64",
        unit="ratio",
        shape=(4,),
        reason="overload",
        metadata={"source": "spawned-worker"},
    )
    assert endpoint.healthy
    endpoint.shutdown()


def test_large_collect_response_uses_binary_frames_without_fencing_worker(
    tmp_path: Path,
) -> None:
    project = _copy_project(tmp_path)
    endpoint = SubprocessInstrumentBackendEndpoint(project, _BACKEND)
    config = load_config()
    [binding] = instrument_bindings(config)
    [expected] = endpoint.describe((binding,)).instruments
    connection = endpoint.connect(
        binding=binding,
        expected=expected,
    )

    receipt = endpoint.collect(
        connection.handle,
        BackendCollectRequest(
            interface_id="tests.control/v1",
            acquisition_id="sample",
            results=(
                BackendCollectResult(
                    request_id="signal",
                    result_id="large_array",
                ),
            ),
        ),
    )

    assert receipt.readback is not None
    value = receipt.readback.values["signal"]
    assert isinstance(value, MeasurementArray)
    assert value.dtype == "string"
    assert value.shape == (1,)
    [text] = value.values
    assert isinstance(text, str)
    assert len(text) == 2 * 1024 * 1024
    assert endpoint.healthy
    endpoint.shutdown()


def test_operation_timeout_fences_generation_and_wakes_pending_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _copy_project(tmp_path)
    endpoint = SubprocessInstrumentBackendEndpoint(project, _BACKEND)
    config = _two_instrument_config()
    bindings = instrument_bindings(config)
    expected = {
        item.instrument_id: item for item in endpoint.describe(bindings).instruments
    }
    bindings_by_id = {binding.id: binding for binding in bindings}
    connections = tuple(
        endpoint.connect(
            binding=bindings_by_id[instrument_id],
            expected=expected[instrument_id],
        )
        for instrument_id in ("source-0", "source-1")
    )
    first_error: list[BaseException] = []
    blocking_request = BackendInvokeRequest(
        interface_id="tests.control/v1",
        operation_id="block",
    )

    def invoke_first_blocking_operation() -> None:
        try:
            endpoint.invoke(
                connections[0].handle,
                blocking_request,
            )
        except BaseException as error:
            first_error.append(error)

    first_invocation = Thread(
        target=invoke_first_blocking_operation,
        daemon=True,
    )
    release_paths = tuple(
        project / f"driver-release-source-{index}" for index in range(2)
    )
    try:
        # Inject the timeout only for the blocking operations under test. Worker
        # setup and OS process cleanup use normal budgets, including on Windows.
        monkeypatch.setattr(endpoint, "_operation_timeout", 1.0)
        first_invocation.start()
        _wait_for_marker(project / "driver-blocked-source-0")
        with pytest.raises(InstrumentBackendUnavailable, match="timed out"):
            endpoint.invoke(
                connections[1].handle,
                blocking_request,
            )
        first_invocation.join(timeout=3)

        assert not first_invocation.is_alive()
        assert len(first_error) == 1
        assert isinstance(first_error[0], InstrumentBackendUnavailable)
        assert "timed out" in str(first_error[0])
        assert not endpoint.healthy
        endpoint.shutdown()
        endpoint.shutdown()
    finally:
        for release in release_paths:
            release.touch()
        endpoint.shutdown()
        first_invocation.join(timeout=3)


def test_shutdown_interrupts_a_blocked_driver_call(tmp_path: Path) -> None:
    project = _copy_project(tmp_path)
    endpoint = SubprocessInstrumentBackendEndpoint(
        project,
        _BACKEND,
        shutdown_timeout=0.1,
    )
    config = load_config()
    [binding] = instrument_bindings(config)
    [expected] = endpoint.describe((binding,)).instruments
    connection = endpoint.connect(
        binding=binding,
        expected=expected,
    )
    errors: list[BaseException] = []

    def invoke_blocking_operation() -> None:
        try:
            endpoint.invoke(
                connection.handle,
                BackendInvokeRequest(
                    interface_id="tests.control/v1",
                    operation_id="block",
                ),
            )
        except BaseException as error:
            errors.append(error)

    invocation = Thread(target=invoke_blocking_operation, daemon=True)
    invocation.start()
    _wait_for_marker(project / "driver-blocked-source-0")

    started_at = time.monotonic()
    endpoint.shutdown()
    elapsed = time.monotonic() - started_at
    invocation.join(timeout=2)

    assert elapsed < 0.5
    assert not invocation.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], InstrumentBackendUnavailable)


def test_runtime_shutdown_fences_a_blocked_session_and_marks_it_unknown(
    tmp_path: Path,
) -> None:
    project = _copy_project(tmp_path)
    endpoint = SubprocessInstrumentBackendEndpoint(
        project,
        _BACKEND,
        shutdown_timeout=0.1,
    )
    runtime = LocalDaemonRuntime(
        project,
        bootstrap_config=load_config(),
        instrument_endpoint=endpoint,
        instrument_shutdown_grace=timedelta(seconds=0.1),
    )
    instruments = runtime.application.instruments
    session = instruments.open_session(
        InstrumentSessionOpenCommand(
            operation_id="open-blocked-session",
            actor="alice",
            instrument_ids=("source-0",),
        )
    )
    invoke_errors: list[BaseException] = []
    close_errors: list[BaseException] = []

    def invoke_blocking_operation() -> None:
        try:
            instruments.invoke(
                session.session_id,
                "source-0",
                InvokeCommand(
                    command_id="blocked-invoke",
                    instrument_id="source-0",
                    resource_id="source-0",
                    interface_id="tests.control/v1",
                    operation_id="block",
                ),
            )
        except BaseException as error:
            invoke_errors.append(error)

    def close_runtime() -> None:
        try:
            runtime.close()
        except BaseException as error:
            close_errors.append(error)

    invocation = Thread(target=invoke_blocking_operation, daemon=True)
    closing = Thread(target=close_runtime, daemon=True)
    try:
        invocation.start()
        _wait_for_marker(project / "driver-blocked-source-0")
        closing.start()
        closing.join(timeout=2)
        invocation.join(timeout=2)
    finally:
        (project / "driver-release-source-0").touch()
        invocation.join(timeout=10)
        endpoint.shutdown()
        runtime.close()
        closing.join(timeout=2)
        invocation.join(timeout=2)

    assert not closing.is_alive()
    assert not invocation.is_alive()
    assert not close_errors
    assert len(invoke_errors) == 1
    assert isinstance(invoke_errors[0], BackendConflict)
    assert str(invoke_errors[0]) == "instrument invoke failed with unknown state"

    control = SQLiteControlPlane(
        SQLiteDatabase(project / ".scopecat" / "control.sqlite3")
    )
    durable = control.get_instrument_session(session.session_id)
    assert durable.state == "attention_required"
    assert durable.attention_reason == "instrument_invoke_unknown"
    assert durable.active_operation_id == "blocked-invoke"
    assert durable.active_operation_kind == "invoke"
    assert durable.end_status is None
    with control.read_transaction() as connection:
        [claim] = control.list_resource_claims_in_transaction(connection)
    assert claim.owner_id == session.session_id
    assert claim.status == "quarantined"


def test_blocked_driver_does_not_block_another_device(tmp_path: Path) -> None:
    project = _copy_project(tmp_path)
    endpoint = SubprocessInstrumentBackendEndpoint(project, _BACKEND)
    config = _two_instrument_config()
    bindings = instrument_bindings(config)
    expected = {
        item.instrument_id: item for item in endpoint.describe(bindings).instruments
    }
    bindings_by_id = {binding.id: binding for binding in bindings}
    first = endpoint.connect(
        binding=bindings_by_id["source-0"],
        expected=expected["source-0"],
    )
    second = endpoint.connect(
        binding=bindings_by_id["source-1"],
        expected=expected["source-1"],
    )
    release = project / "driver-release-source-0"

    try:
        with ThreadPoolExecutor(max_workers=2) as calls:
            blocked = calls.submit(
                endpoint.invoke,
                first.handle,
                BackendInvokeRequest(
                    interface_id="tests.control/v1",
                    operation_id="block",
                ),
            )
            _wait_for_marker(project / "driver-blocked-source-0")
            independent = calls.submit(
                endpoint.read_state,
                second.handle,
                _gain_read_request(),
            )
            try:
                state = independent.result(timeout=10)
            finally:
                release.touch()
            blocked.result(timeout=10)
        assert state.instrument_id == "source-1"
        [observation] = state.observations
        assert observation.metadata["worker_pid"] == endpoint.worker_pid
    finally:
        release.touch()
        endpoint.shutdown()


def _copy_project(root: Path) -> Path:
    project = root / "project"
    project.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_FIXTURE, project)
    return project


def _two_instrument_config() -> ConfigProfileSnapshot:
    config = load_config()
    [source] = config.instrument_registry.instruments
    registry = config.instrument_registry.model_copy(
        update={
            "instruments": [
                source,
                source.model_copy(
                    update={
                        "id": "source-1",
                        "exclusivity_key": "source-1",
                    }
                ),
            ]
        }
    )
    return config.model_copy(
        update={
            "system": config.system.model_copy(update={"instrument_registry": registry})
        }
    )


def _ragged_point_cloud_config() -> ConfigProfileSnapshot:
    config = load_config()
    [route] = config.routing.routes[:1]
    [endpoint] = route.endpoints[:1]
    routing = config.routing.model_copy(
        update={
            "routes": [
                route.model_copy(
                    update={
                        "endpoints": [
                            endpoint.model_copy(
                                update={"interface_id": "tests.control/v1"}
                            )
                        ]
                    }
                )
            ]
        }
    )
    return config.model_copy(
        update={"system": config.system.model_copy(update={"routing": routing})}
    )


def _http_daemon_client(transport: TestClient) -> DaemonClient:
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

    return DaemonClient(
        "http://testserver",
        transport=httpx2.MockTransport(send),
    )


def _wait_for_marker(path: Path) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() >= deadline:
            pytest.fail(f"fixture driver did not create {path.name}")
        time.sleep(0.01)


def test_instrument_startup_timeout_retains_child_phase_and_reaps_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scopecat_server.instruments import worker

    project = _copy_project(tmp_path)
    (project / "src/worker_fixture/backend.py").write_text(
        "import time\ndef create_backend(project_root):\n"
        "    while True:\n        time.sleep(1)\n",
        encoding="utf-8",
    )
    diagnostics = tmp_path / "startup"
    monkeypatch.setenv("SCOPECAT_STARTUP_DIAGNOSTICS", str(diagnostics))
    stopped: list[tuple[int | None, int | None, bool]] = []
    terminate = worker._terminate_process_until

    def observe_termination(process: BaseProcess, deadline: float) -> None:
        terminate(process, deadline)
        stopped.append((process.pid, process.exitcode, process.is_alive()))

    monkeypatch.setattr(worker, "_terminate_process_until", observe_termination)
    with pytest.raises(InstrumentBackendUnavailable, match="did not start in time"):
        SubprocessInstrumentBackendEndpoint(project, _BACKEND, startup_timeout=10)
    assert len(stopped) == 1
    pid, exitcode, alive = stopped[0]
    assert exitcode is not None and not alive
    [trace] = diagnostics.glob("instrument-startup-*.log")
    assert trace.name == f"instrument-startup-{pid}.log"
    text = trace.read_text(encoding="utf-8")
    assert "python entry; pid=" in text and "parent=" in text
    assert "output capture ready; importing RPC runtime" in text
    assert "backend factory loaded; constructing backend" in text
    assert "Timeout (0:00:05)" in text
    assert "readiness sent" not in text
