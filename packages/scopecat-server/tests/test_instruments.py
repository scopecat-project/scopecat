from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Literal, Never, cast, override

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.api.instruments import (
    InstrumentClientChannel,
    InstrumentRef,
    InstrumentSessionHandle,
    instrument,
    temporary_instrument,
)
from scopecat.api.lab import LabClient
from scopecat.control.models import RunPlanSummary, RunResourceRequirement
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.views import ActiveConfigView
from scopecat.daemon.wire import (
    ConfigPublishCommand,
    DirectConfigRevisionSource,
    ExecutorStartRequest,
    InstrumentConfiguredDefaultsApplyCommand,
    InstrumentDriverProbeCommand,
    InstrumentSessionOpenCommand,
    RunSubmission,
)
from scopecat.kernel.problems import ProblemPhase, model_location, problem
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.state import StateValue
from scopecat.records.config import (
    ConfigProfileSnapshot,
    InstrumentBindingSpec,
    InstrumentRunStartPolicy,
    TcpipSocketInstrumentConnection,
    VirtualInstrumentConnection,
    config_content_hash,
    instrument_bindings,
)
from scopecat.records.content import command_payload_from_bytes
from scopecat.records.instrument import (
    InstrumentStateSetting,
    state_member_target,
    state_setting,
)
from scopecat.records.measurement import (
    MeasurementAcquisitionValue,
    MeasurementScalar,
)
from scopecat.records.run_request import RunRequest
from scopecat.sdk.instruments import (
    AcquisitionResultRef,
    DriverAcquisition,
    DriverCatalog,
    DriverConnectionSpec,
    DriverFault,
    DriverOperation,
    DriverOutcome,
    DriverPayload,
    DriverReadback,
    DriverRejected,
    DriverScalar,
    DriverSpec,
    DriverStateObservation,
    DriverStatePatch,
    DriverStateReadback,
    DriverStateReadRequest,
    DriverSuccess,
    DriverUnknown,
    InstrumentBackend,
    InstrumentConnectionContext,
    InstrumentDescription,
    InstrumentDriver,
    InstrumentProvider,
    InstrumentProviderContext,
    InstrumentProviderDescription,
    InterfaceRef,
    PropertyRef,
    acquisition,
    acquisition_result,
    bool_property,
    enum_property,
    float_property,
    interface,
    quantity_property,
    state_readback,
)
from scopecat.sdk.instruments.commands import (
    InstrumentStateAssignment,
    InstrumentStateCommand,
    InstrumentStateReadCommand,
    InteractiveCollectIntent,
)
from scopecat_testkit.instrument_drivers import SignalInstrumentDriver, load_config
from scopecat_testkit.payload_codecs import json_payload_codecs

import scopecat_server.instruments.runtime as instrument_service_module
from scopecat_server import LocalDaemonRuntime
from scopecat_server.errors import BackendConflict
from scopecat_server.instruments.backend import LocalInstrumentBackendEndpoint

_SET_FREQUENCY = InterfaceRef("test.set_frequency/v1").property("frequency")
_PLAY_PROGRAM = InterfaceRef("test.play_program/v1").operation("play")
_PLAY_PROGRAM_ARGUMENT = _PLAY_PROGRAM.argument("program")
_SAMPLE_SIGNAL = InterfaceRef("test.scalar_signal/v1").acquisition("sample")
_DC = InterfaceRef("test.dc/v1")
_DC_MODE = _DC.property("mode")
_DC_VOLTAGE_LEVEL = _DC.property("voltage_level")
_DC_CURRENT_LEVEL = _DC.property("current_level")
_SESSION_LEASE_TTL = timedelta(seconds=90)


def _setting(
    *,
    interface_id: str,
    property_id: str,
    value: StateValue,
    component_path: Sequence[str] = (),
) -> InstrumentStateSetting:
    return state_setting(
        PropertyRef(interface_id, tuple(component_path), property_id),
        value,
    )


class _TrackingDriver(SignalInstrumentDriver):
    def __init__(self, instrument_id: str) -> None:
        super().__init__(instrument_id=instrument_id)
        self.disconnected = False
        self.aborted = False
        self.disconnect_count = 0
        self.abort_count = 0

    @override
    def disconnect(self) -> None:
        self.disconnected = True
        self.disconnect_count += 1

    @override
    def abort(self) -> None:
        self.aborted = True
        self.abort_count += 1


class _TrackingProvider:
    provider_id = "tests.interactive_provider"

    def __init__(
        self,
        driver_type: type[_TrackingDriver] = _TrackingDriver,
    ) -> None:
        self._driver_type = driver_type
        self.drivers: list[_TrackingDriver] = []
        self.described_bindings: list[tuple[InstrumentBindingSpec, ...]] = []

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        self.described_bindings.append(context.bindings)
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=tuple(
                self._driver_type(instrument_id).describe()
                for instrument_id in _selected_ids(context)
            ),
        )

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> InstrumentDriver:
        driver = self._driver_type(context.binding.id)
        self.drivers.append(driver)
        return driver


class _ToggleDescriptionProvider(_TrackingProvider):
    def __init__(
        self,
        driver_type: type[_TrackingDriver] = _TrackingDriver,
    ) -> None:
        super().__init__(driver_type)
        self.description_available = True

    @override
    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        if not self.description_available:
            raise AssertionError("open retry resolved the replacement config")
        return super().describe(context)


class _UpdatedContractDriver(_TrackingDriver):
    def __init__(self, instrument_id: str) -> None:
        super().__init__(instrument_id)
        self.implementation_version = "v1"


class _RejectedProvider(_TrackingProvider):
    @override
    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> InstrumentDriver:
        del context
        raise DriverFault(
            problem(
                "slow_rejection",
                "the provider rejected the connection",
                phase=ProblemPhase.PROVIDER_PREFLIGHT,
                location=model_location("instrument_provider"),
            )
        )


class _ReadFailDriver(_TrackingDriver):
    @override
    def read_state(self, request: DriverStateReadRequest) -> Never:
        del request
        raise RuntimeError("read transport failed")


class _AbortFailDriver(_TrackingDriver):
    @override
    def abort(self) -> Never:
        self.aborted = True
        self.abort_count += 1
        raise RuntimeError("abort transport failed")


class _ResyncDriver(_TrackingDriver):
    def __init__(self, instrument_id: str) -> None:
        super().__init__(instrument_id)
        self.read_count = 0
        self.fail_next_read = False
        self.return_invalid_next_read = False

    def change_from_front_panel(self, value: float) -> None:
        self._state[("test.set_frequency/v1", "frequency")] = Quantity(
            value=value, unit="GHz"
        )

    @override
    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        self.read_count += 1
        if self.fail_next_read:
            self.fail_next_read = False
            raise RuntimeError("stale connection")
        if self.return_invalid_next_read:
            self.return_invalid_next_read = False
            return state_readback(request, {_SET_FREQUENCY: float("nan")})
        return super().read_state(request)


class _InvalidCollectDriver(_TrackingDriver):
    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        self.collect_requests.append(request)
        return DriverSuccess(
            DriverReadback(
                values={
                    result: MeasurementScalar.create(
                        dtype="float64",
                        value=1.0,
                        unit="K",
                    )
                    for result in request.results
                }
            )
        )


class _InvokeReadbackDriver(_TrackingDriver):
    def __init__(self, instrument_id: str) -> None:
        super().__init__(instrument_id)
        self.read_count = 0

    @override
    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        self.read_count += 1
        return super().read_state(request)

    @override
    def invoke(
        self,
        request: DriverOperation,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.invoked.append(request)
        return DriverSuccess(None)


class _NonConvergingApplyDriver(_ResyncDriver):
    @override
    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.applied.append(request)
        return DriverSuccess(None)


class _NotAppliedDriver(_ResyncDriver):
    @override
    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.applied.append(request)
        return DriverRejected(
            problems=(
                problem(
                    "driver_rejected_apply",
                    "the driver rejected the requested state",
                    phase=ProblemPhase.EXECUTION,
                ),
            ),
        )


class _UnknownApplyDriver(_ResyncDriver):
    @override
    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.applied.append(request)
        return DriverUnknown(
            problems=(
                problem(
                    "driver_apply_unknown",
                    "the driver could not determine whether the state changed",
                    phase=ProblemPhase.EXECUTION,
                ),
            ),
        )


class _StatefulDriver(_TrackingDriver):
    def __init__(
        self,
        instrument_id: str,
        state: dict[tuple[str, str], DriverScalar],
    ) -> None:
        super().__init__(instrument_id)
        for target, value in self._state.items():
            state.setdefault(target, value)
        self._state = state


class _VariantDriver(_TrackingDriver):
    def __init__(self, instrument_id: str) -> None:
        super().__init__(instrument_id)
        self.mode = "voltage"
        self.voltage_level = 0.1
        self.current_level = 0.01
        self.read_count = 0

    @override
    def describe(self) -> InstrumentDescription:
        base = super().describe()
        return base.model_copy(
            update={
                "interfaces": [
                    *base.interfaces,
                    interface(
                        "test.dc/v1",
                        properties=[
                            enum_property(
                                "mode",
                                choices=("voltage", "current"),
                            ),
                            bool_property("output_enabled"),
                            float_property("voltage_level"),
                            float_property("current_level"),
                        ],
                        acquisitions=[
                            acquisition(
                                "measure",
                                results=(
                                    acquisition_result(
                                        "monitored_voltage",
                                        unit="V",
                                    ),
                                    acquisition_result(
                                        "monitored_current",
                                        unit="A",
                                    ),
                                ),
                            )
                        ],
                    ),
                ]
            }
        )

    @override
    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        self.read_count += 1
        base = super().read_state(request)
        readback = state_readback(
            request,
            {
                **base.values,
                _DC_MODE: self.mode,
                _DC_VOLTAGE_LEVEL: self.voltage_level,
                _DC_CURRENT_LEVEL: self.current_level,
                _DC.property("output_enabled"): False,
            },
        )
        if not base.observations:
            return readback
        return readback.with_observation_metadata(base.observations[0].metadata)

    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        self.collect_requests.append(request)
        active_result = (
            "monitored_voltage" if self.mode == "voltage" else "monitored_current"
        )
        if {result.result_id for result in request.results} != {active_result}:
            return DriverRejected(
                problems=(
                    problem(
                        "test_acquisition_result_inactive",
                        f"{self.mode} mode provides only {active_result}",
                        phase=ProblemPhase.EXECUTION,
                    ),
                ),
            )
        values: dict[AcquisitionResultRef, MeasurementAcquisitionValue] = {
            result: (
                MeasurementScalar.create(
                    dtype="float64",
                    value=self.voltage_level,
                    unit="V",
                )
                if result.result_id == "monitored_voltage"
                else MeasurementScalar.create(
                    dtype="float64",
                    value=self.current_level,
                    unit="A",
                )
            )
            for result in request.results
        }
        return DriverSuccess(DriverReadback(values=values))

    @override
    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.applied.append(request)
        for entry in request.entries:
            target = entry.target
            value = entry.value
            if target.property_id == "mode":
                assert isinstance(value, str)
                self.mode = value
            elif target.property_id == "voltage_level":
                assert isinstance(value, float)
                self.voltage_level = value
            elif target.property_id == "current_level":
                assert isinstance(value, float)
                self.current_level = value
        return DriverSuccess(None)


class _StatefulProvider:
    provider_id = "tests.stateful_interactive_provider"

    def __init__(self) -> None:
        self.state: dict[tuple[str, str], DriverScalar] = {}

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=tuple(
                _StatefulDriver(instrument_id, self.state).describe()
                for instrument_id in _selected_ids(context)
            ),
        )

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> InstrumentDriver:
        return _StatefulDriver(context.binding.id, self.state)


class _ShutdownRaceDriver(_TrackingDriver):
    def __init__(
        self,
        instrument_id: str,
        *,
        read_entered: Event,
        release_read: Event,
        abort_entered: Event,
        release_abort: Event,
    ) -> None:
        super().__init__(instrument_id)
        self._read_entered = read_entered
        self._release_read = release_read
        self._abort_entered = abort_entered
        self._release_abort = release_abort

    @override
    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        if self.instrument_id == "source-1":
            self._read_entered.set()
            assert self._release_read.wait(timeout=3)
        return super().read_state(request)

    @override
    def abort(self) -> None:
        self.aborted = True
        self.abort_count += 1
        if self.instrument_id == "source-0":
            self._abort_entered.set()
            assert self._release_abort.wait(timeout=3)


class _ShutdownRaceProvider:
    provider_id = "tests.shutdown_race_provider"

    def __init__(self) -> None:
        self.read_entered = Event()
        self.release_read = Event()
        self.abort_entered = Event()
        self.release_abort = Event()
        self.drivers: list[_ShutdownRaceDriver] = []

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=tuple(
                _TrackingDriver(instrument_id).describe()
                for instrument_id in _selected_ids(context)
            ),
        )

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> InstrumentDriver:
        driver = _ShutdownRaceDriver(
            context.binding.id,
            read_entered=self.read_entered,
            release_read=self.release_read,
            abort_entered=self.abort_entered,
            release_abort=self.release_abort,
        )
        self.drivers.append(driver)
        return driver


class _ProblemProvider(_TrackingProvider):
    @override
    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        description = super().describe(context)
        return InstrumentProviderDescription(
            provider_id=description.provider_id,
            instruments=description.instruments,
            problems=(
                problem(
                    "source_zero_problem",
                    "source zero is misconfigured",
                    phase=ProblemPhase.PROVIDER_PREFLIGHT,
                    location=model_location("instrument_provider"),
                    details={"instrument_id": "source-0"},
                ),
                problem(
                    "source_one_problem",
                    "source one is misconfigured",
                    phase=ProblemPhase.PROVIDER_PREFLIGHT,
                    location=model_location(
                        "instrument_provider",
                        "config",
                        "system",
                        "instrument_registry",
                        "instruments",
                        1,
                    ),
                ),
                problem(
                    "provider_global_problem",
                    "provider discovery is incomplete",
                    phase=ProblemPhase.PROVIDER_PREFLIGHT,
                    location=model_location("instrument_provider"),
                ),
            ),
        )


class _ScopedProblemProvider(_ProblemProvider):
    @override
    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        description = super().describe(context)
        return InstrumentProviderDescription(
            provider_id=description.provider_id,
            instruments=description.instruments,
            problems=tuple(
                item
                for item in description.problems
                if item.code == "source_zero_problem"
            ),
        )


def test_instrument_views_expose_only_safe_configuration_summaries(
    tmp_path: Path,
) -> None:
    config = _config_with_private_instrument_settings()
    with (
        _runtime(tmp_path, _TrackingProvider(), config=config) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        list_response = transport.get("/api/v1/instruments")
        detail_response = transport.get("/api/v1/instruments/source-0")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    payload = list_response.json()
    assert set(payload) == {"config_entry_id", "items", "problems"}
    by_id = {item["instrument_id"]: item for item in payload["items"]}
    assert by_id["source-0"]["driver_id"] == "tests.signal_instrument"
    assert by_id["source-0"]["connection"] == {
        "kind": "tcpip_socket",
        "host": "instrument.example",
        "port": 5025,
    }
    assert by_id["source-1"]["connection"] == {"kind": "virtual"}
    assert detail_response.json() == by_id["source-0"]
    for forbidden in (
        "spec",
        "exclusivity_key",
        "config_content_hash",
        "timeout_seconds",
        "options",
        "default_state",
        "run_start",
        "success_action",
        "private-token",
    ):
        assert forbidden not in list_response.text
        assert forbidden not in detail_response.text


def test_driver_probe_uses_an_ephemeral_worker_connection(tmp_path: Path) -> None:
    config = load_config()
    [binding] = instrument_bindings(config)
    provider = _TrackingProvider()
    catalog = DriverCatalog(
        provider_id=provider.provider_id,
        drivers=(
            DriverSpec(
                driver_id=binding.driver_id,
                implementation_version="v1",
                label="Test signal instrument",
                connections=(
                    DriverConnectionSpec(
                        kind=binding.connection.kind,
                        options_schema={
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    ),
                ),
            ),
        ),
    )
    with (
        _runtime(tmp_path, provider, config=config, driver_catalog=catalog) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        receipt = _daemon_client(transport).probe_driver(
            InstrumentDriverProbeCommand(binding=binding)
        )

    assert receipt.status == "connected"
    assert receipt.description is not None
    assert receipt.description.instrument_id == binding.id
    [driver] = provider.drivers
    assert driver.disconnect_count == 1


def test_notebook_direct_interaction_releases_ownership_but_keeps_connection(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            lab = LabClient(daemon, operator="default-operator")

            [available] = lab.instruments.list().items
            assert available.availability == "available"

            with lab.instruments.open(_raw_instrument("source-0")) as session:
                description = session._describe()
                state_receipt = session._apply(
                    {_SET_FREQUENCY: Quantity(value=5.1, unit="GHz")}
                )
                payload = command_payload_from_bytes(
                    id="program-1",
                    schema_id="pulse_program",
                    codec_id="tests.canonical-json",
                    codec_version=1,
                    media_type="application/json",
                    content=b'{"samples":[0.0]}',
                )
                invoke_receipt = session._invoke(
                    _PLAY_PROGRAM,
                    {_PLAY_PROGRAM_ARGUMENT: payload},
                )
                collect_receipt = session._collect(
                    _SAMPLE_SIGNAL,
                    _SAMPLE_SIGNAL.result("signal"),
                )
                [owned] = lab.instruments.list().items

                assert description.instrument_id == "source-0"
                assert state_receipt.status == "applied"
                assert invoke_receipt.status == "invoked"
                assert invoke_receipt.readback is None
                assert collect_receipt.status == "collected"
                assert owned.availability == "active"
                assert owned.owner_kind == "instrument_session"
                assert owned.owner_actor == "default-operator"

            [released] = lab.instruments.list().items
            [driver] = provider.drivers
            assert released.availability == "available"
            assert not driver.disconnected
            [applied_target] = driver.applied[0].values
            assert applied_target.property_id == "frequency"
            assert driver.invoked[0].target.operation_id == "play"
            [argument] = driver.invoked[0].arguments.values()
            assert isinstance(argument, DriverPayload)
            assert argument.schema_id == payload.schema_id
            assert argument.value == {"samples": [0.0]}
            [collect_request] = driver.collect_requests
            assert collect_request.target.acquisition_id == "sample"
            assert [result.result_id for result in collect_request.results] == [
                "signal"
            ]


def test_exact_observed_member_reads_the_current_actor_cache_without_hardware_io(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-exact-observed-member",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            command = InstrumentStateReadCommand(
                targets=[state_member_target(_SET_FREQUENCY)]
            )
            [driver] = provider.drivers
            assert isinstance(driver, _ResyncDriver)
            assert driver.read_count == 1

            initial_cache = daemon.read_observed_instrument_state_members(
                session.session_id,
                "source-0",
                command,
            )
            [initial] = initial_cache.entries
            assert initial.status == "observed"
            assert initial.observation is not None
            assert initial.observation.value.root == Quantity(value=4.0, unit="GHz")
            assert driver.read_count == 1

            driver.change_from_front_panel(5.1)
            cached_cache = daemon.read_observed_instrument_state_members(
                session.session_id,
                "source-0",
                command,
            )
            [cached] = cached_cache.entries
            assert cached.status == "observed"
            assert cached.observation is not None
            assert cached.observation.value.root == Quantity(value=4.0, unit="GHz")
            assert cached_cache.generation == initial_cache.generation
            assert driver.read_count == 1

            [fresh] = daemon.read_instrument_state_members(
                session.session_id,
                "source-0",
                command,
            ).observations
            assert fresh.value.root == Quantity(value=5.1, unit="GHz")
            assert driver.read_count == 2

            observed_cache = daemon.read_observed_instrument_state_members(
                session.session_id,
                "source-0",
                command,
            )
            [observed] = observed_cache.entries
            assert observed.status == "observed"
            assert observed.observation is not None
            assert observed.observation.value.root == Quantity(
                value=5.1,
                unit="GHz",
            )
            assert observed_cache.generation > cached_cache.generation
            assert driver.read_count == 2
            daemon.close_instrument_session(session.session_id)


def test_notebook_can_attach_a_session_only_instrument(tmp_path: Path) -> None:
    provider = _TrackingProvider()
    config = load_config()
    [configured] = config.instrument_registry.instruments
    catalog = DriverCatalog(
        provider_id=provider.provider_id,
        drivers=(
            DriverSpec(
                driver_id=configured.driver_id,
                implementation_version="v1",
                label="Temporary signal instrument",
                connections=(
                    DriverConnectionSpec(
                        kind="virtual",
                        options_schema={
                            "type": "object",
                            "properties": {},
                            "additionalProperties": True,
                        },
                    ),
                ),
            ),
        ),
    )
    monitor = temporary_instrument(
        _raw_instrument("monitor-scope"),
        driver_id=configured.driver_id,
        connection=VirtualInstrumentConnection(
            options={"purpose": "inspect-awg-output"}
        ),
    )

    with (
        _runtime(tmp_path, provider, config=config, driver_catalog=catalog) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        lab = LabClient(_daemon_client(transport), operator="debugger")

        with lab.instruments.open(monitor) as session:
            assert session[monitor] is session
            assert session._describe().instrument_id == "monitor-scope"
            assert session._observed_state().instrument_id == "monitor-scope"
            assert {item.instrument_id for item in lab.instruments.list().items} == {
                "source-0"
            }

    [driver] = provider.drivers
    assert driver.instrument_id == "monitor-scope"
    assert driver.disconnect_count == 1


def test_invoke_without_invalidations_does_not_force_state_readback(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_InvokeReadbackDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            handle = LabClient(_daemon_client(transport)).instruments.open(
                _raw_instrument("source-0"),
            )
            handle._observed_state()
            [driver] = provider.drivers
            assert isinstance(driver, _InvokeReadbackDriver)
            reads_before_invoke = driver.read_count
            payload = command_payload_from_bytes(
                id="program-1",
                schema_id="pulse_program",
                codec_id="tests.canonical-json",
                codec_version=1,
                media_type="application/json",
                content=b'{"samples":[0.0]}',
            )

            receipt = handle._invoke(
                _PLAY_PROGRAM,
                {_PLAY_PROGRAM_ARGUMENT: payload},
            )
            handle.close()

            assert receipt.status == "invoked"
            assert receipt.readback is None
            assert driver.read_count == reads_before_invoke


def test_apply_without_reported_state_reads_back_before_returning(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            handle = LabClient(_daemon_client(transport)).instruments.open(
                _raw_instrument("source-0"),
            )
            handle._observed_state()
            [driver] = provider.drivers
            assert isinstance(driver, _ResyncDriver)
            reads_before_apply = driver.read_count

            receipt = handle._apply(
                {_SET_FREQUENCY: Quantity(value=5.1, unit="GHz")},
            )
            handle.close()

            assert receipt.status == "applied"
            assert receipt.readback is None
            assert driver.read_count == reads_before_apply + 2


def test_apply_readback_must_confirm_the_requested_state(tmp_path: Path) -> None:
    provider = _TrackingProvider(_NonConvergingApplyDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-non-converging",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )

            with pytest.raises(
                DaemonConflictError,
                match="readback did not match requested state",
            ):
                daemon.apply_instrument_state(
                    session.session_id,
                    "source-0",
                    _apply_command(value=5.1),
                )

            [driver] = provider.drivers
            assert isinstance(driver, _NonConvergingApplyDriver)
            [instrument] = daemon.list_instruments().items
            assert len(driver.applied) == 1
            assert driver.read_count == 3
            assert driver.disconnect_count == 1
            assert instrument.availability == "quarantined"


def test_interactive_apply_validates_against_fresh_device_state(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_VariantDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            handle = LabClient(_daemon_client(transport)).instruments.open(
                _raw_instrument("source-0"),
            )
            handle._observed_state()
            [driver] = provider.drivers
            assert isinstance(driver, _VariantDriver)
            driver.mode = "current"

            receipt = handle._apply({_DC_CURRENT_LEVEL: 0.03})
            handle.close()

            assert receipt.status == "applied"
            assert receipt.readback is None
            assert driver.current_level == 0.03
            assert driver.read_count == 3


def test_collect_replay_precedes_fresh_state_validation(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_VariantDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-collect-replay-after-state-change",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            intent = _variant_collect_intent(
                command_id="collect-voltage-before-state-change",
                result_ids=("monitored_voltage",),
            )

            first = daemon.collect_instrument(
                session.session_id,
                "source-0",
                intent,
            )
            [driver] = provider.drivers
            assert isinstance(driver, _VariantDriver)
            assert driver.read_count == 2
            driver.mode = "current"

            replay = daemon.collect_instrument(
                session.session_id,
                "source-0",
                intent,
            )
            assert driver.read_count == 2
            with pytest.raises(
                DaemonConflictError,
                match="different collect content",
            ):
                daemon.collect_instrument(
                    session.session_id,
                    "source-0",
                    intent.model_copy(
                        update={"result_ids": ["monitored_current"]},
                    ),
                )

            assert replay == first
            assert len(driver.collect_requests) == 1
            assert driver.read_count == 2
            daemon.close_instrument_session(session.session_id)


def test_collect_driver_rejection_is_replayed_without_hardware_io(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_VariantDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-collect-rejection-replay",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            [driver] = provider.drivers
            assert isinstance(driver, _VariantDriver)
            intent = _variant_collect_intent(
                command_id="collect-inactive-current",
                result_ids=("monitored_current",),
            )

            first = daemon.collect_instrument(
                session.session_id,
                "source-0",
                intent,
            )
            assert driver.read_count == 3
            driver.mode = "current"
            replay = daemon.collect_instrument(
                session.session_id,
                "source-0",
                intent,
            )
            assert driver.read_count == 3
            collected = daemon.collect_instrument(
                session.session_id,
                "source-0",
                intent.model_copy(update={"command_id": "collect-active-current"}),
            )

            assert first.status == "not_collected"
            assert [item.code for item in first.problems] == [
                "test_acquisition_result_inactive"
            ]
            assert replay == first
            assert collected.status == "collected"
            assert len(driver.collect_requests) == 2
            assert driver.read_count == 4
            daemon.close_instrument_session(session.session_id)


def test_operation_retry_is_deduplicated_and_conflicting_content_is_rejected(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-1",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            command = _apply_command(value=5.0)

            first = daemon.apply_instrument_state(
                session.session_id,
                "source-0",
                command,
            )
            second = daemon.apply_instrument_state(
                session.session_id,
                "source-0",
                command,
            )

            assert second == first
            [driver] = provider.drivers
            assert len(driver.applied) == 1
            with pytest.raises(DaemonConflictError, match="different apply content"):
                daemon.apply_instrument_state(
                    session.session_id,
                    "source-0",
                    _apply_command(value=5.2),
                )
            daemon.close_instrument_session(session.session_id)


def test_apply_rejects_logical_assignments_for_one_physical_property(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-duplicate-physical-property",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            payload = _apply_command(value=5.1).model_dump(mode="json")
            duplicate = dict(payload["assignments"][0])
            duplicate["entity_ids"] = ["sample"]
            payload["assignments"].append(duplicate)

            response = transport.post(
                "/api/v1/instrument-sessions/"
                f"{session.session_id}/instruments/source-0/state/apply",
                json=payload,
            )

            assert response.status_code == 422
            assert "property targets must be unique" in response.text
            [driver] = provider.drivers
            assert driver.applied == []
            daemon.close_instrument_session(session.session_id)


def test_open_retry_reuses_session_without_reprovisioning(tmp_path: Path) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            command = InstrumentSessionOpenCommand(
                operation_id="open-retry",
                actor="alice",
                instrument_ids=("source-0",),
            )

            first = daemon.open_instrument_session(command)
            [driver] = provider.drivers
            assert isinstance(driver, _ResyncDriver)
            assert driver.read_count == 1
            driver.change_from_front_panel(5.1)

            second = daemon.open_instrument_session(command)

            assert second == first
            assert second.observed_state == first.observed_state
            assert driver.read_count == 1
            assert len(provider.drivers) == 1
            daemon.close_instrument_session(first.session_id)


def test_open_retry_recovers_before_resolving_replacement_config(
    tmp_path: Path,
) -> None:
    provider = _ToggleDescriptionProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            original = runtime.application.config.get_active_config()
            command = InstrumentSessionOpenCommand(
                operation_id="open-retry-after-config-activation",
                actor="alice",
                instrument_ids=("source-0",),
            )

            def replace_active_config() -> None:
                updated = load_config().model_copy(update={"id": "updated-config"})
                runtime.application.config.publish_config(
                    ConfigPublishCommand(
                        operation_id="publish:updated-config-after-open",
                        source=DirectConfigRevisionSource(config=updated),
                        entry_id="updated-config",
                        actor="operator",
                        expected_generation=1,
                    )
                )
                provider.description_available = False

            daemon = _daemon_client(
                transport,
                drop_response_suffix="/instrument-sessions",
                on_response_drop=replace_active_config,
            )
            session = daemon.open_instrument_session(command)

            [durable] = runtime.application.executor._control.list_instrument_sessions()
            assert session.session_id == durable.session_id
            assert session.config_entry_id == original.entry.id
            assert session.config_content_hash == original.entry.content_hash
            assert len(provider.drivers) == 1
            daemon.close_instrument_session(session.session_id)


def test_sequential_sessions_reuse_connection_but_not_state_or_replay_scope(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            first = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-owner-1",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            daemon.apply_instrument_state(
                first.session_id,
                "source-0",
                _apply_command(value=5.0),
            )
            daemon.close_instrument_session(first.session_id)

            [driver] = provider.drivers
            assert isinstance(driver, _ResyncDriver)
            assert driver.read_count == 3
            driver.change_from_front_panel(5.1)

            second = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-owner-2",
                    actor="bob",
                    instrument_ids=("source-0",),
                )
            )
            assert provider.drivers == [driver]
            assert driver.read_count == 4
            second_apply = daemon.apply_instrument_state(
                second.session_id,
                "source-0",
                _apply_command(value=5.2),
            )
            daemon.close_instrument_session(second.session_id)

            assert second_apply.status == "applied"
            assert driver.read_count == 6
            assert len(driver.applied) == 2
            assert [
                next(iter(request.values.values())) for request in driver.applied
            ] == [
                Quantity(value=5.0, unit="GHz"),
                Quantity(value=5.2, unit="GHz"),
            ]
            assert driver.disconnect_count == 0


def test_expired_session_releases_owner_and_reuses_fresh_connection(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    with (
        _runtime(
            tmp_path,
            provider,
            session_lease_ttl=timedelta(seconds=30),
        ) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        first = daemon.open_instrument_session(
            InstrumentSessionOpenCommand(
                operation_id="open-expiring-owner",
                actor="alice",
                instrument_ids=("source-0",),
            )
        )
        renewed = daemon.renew_instrument_session(first.session_id)

        runtime.application.instruments.expire_leases(at=first.expires_at)
        assert (
            runtime.application.executor._control.get_instrument_session(
                first.session_id
            ).state
            == "active"
        )

        runtime.application.instruments.expire_leases(at=renewed.expires_at)
        assert (
            runtime.application.executor._control.get_instrument_session(
                first.session_id
            ).state
            == "closed"
        )
        with pytest.raises(DaemonConflictError, match="not active"):
            daemon.renew_instrument_session(first.session_id)
        [driver] = provider.drivers
        assert isinstance(driver, _ResyncDriver)
        assert driver.abort_count == 0
        assert driver.disconnect_count == 0

        driver.change_from_front_panel(5.1)
        second = daemon.open_instrument_session(
            InstrumentSessionOpenCommand(
                operation_id="open-after-owner-expiry",
                actor="bob",
                instrument_ids=("source-0",),
            )
        )

        assert provider.drivers == [driver]
        assert driver.read_count == 2
        frequency = next(
            property_.value.root
            for property_ in second.observed_state[0].observations
            if property_.target.property_id == "frequency"
        )
        assert frequency == Quantity(value=5.1, unit="GHz")
        daemon.close_instrument_session(second.session_id)


def test_session_expiry_during_recorded_operation_quarantines_owner(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-expiring-operation",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            control = runtime.application.executor._control
            control.start_instrument_operation(
                session.session_id,
                instrument_id="source-0",
                operation_id="in-flight-operation",
                kind="invoke",
            )

            runtime.application.instruments.expire_leases(at=session.expires_at)

            durable = control.get_instrument_session(session.session_id)
            assert durable.state == "attention_required"
            assert (
                durable.attention_reason
                == "instrument_session_lease_expired_during_operation"
            )
            with control.read_transaction() as connection:
                [claim] = control.list_resource_claims_in_transaction(connection)
            assert claim.status == "quarantined"
            [driver] = provider.drivers
            assert driver.abort_count == 1
            assert driver.disconnect_count == 1


def test_config_activation_reuses_matching_connection_with_fresh_state(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-before-config-activation",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            [driver] = provider.drivers
            assert isinstance(driver, _ResyncDriver)
            assert driver.read_count == 1
            updated = _config_with_default_state(
                _setting(
                    interface_id="test.set_frequency/v1",
                    property_id="frequency",
                    value=StateValue(Quantity(value=5.0, unit="GHz")),
                )
            ).model_copy(update={"id": "updated-config"})

            receipt = runtime.application.config.publish_config(
                ConfigPublishCommand(
                    operation_id="publish:updated-config-session-state",
                    source=DirectConfigRevisionSource(config=updated),
                    entry_id="updated-config",
                    actor="operator",
                    expected_generation=1,
                )
            )

            assert receipt.activation.generation == 2
            assert driver.disconnect_count == 0
            daemon.close_instrument_session(session.session_id)
            driver.change_from_front_panel(5.1)
            reopened = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-after-config-activation",
                    actor="bob",
                    instrument_ids=("source-0",),
                )
            )
            assert provider.drivers == [driver]
            assert driver.read_count == 2
            assert driver.disconnect_count == 0
            daemon.close_instrument_session(reopened.session_id)


def test_session_open_fences_an_activation_after_active_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _TrackingProvider()
    config = load_config()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            get_active = runtime.application.config.get_active_config

            def resolve_then_activate() -> ActiveConfigView:
                resolved = get_active()
                runtime.application.config.publish_config(
                    ConfigPublishCommand(
                        operation_id="publish:activated-during-open",
                        source=DirectConfigRevisionSource(
                            config=config.model_copy(
                                update={"id": "activated-during-open"}
                            )
                        ),
                        entry_id="activated-during-open",
                        actor="operator",
                        expected_generation=resolved.activation.generation,
                    )
                )
                return resolved

            monkeypatch.setattr(
                runtime.application.config,
                "get_active_config",
                resolve_then_activate,
            )
            daemon = _daemon_client(transport)

            with pytest.raises(
                DaemonConflictError,
                match="active configuration changed",
            ):
                daemon.open_instrument_session(
                    InstrumentSessionOpenCommand(
                        operation_id="open-during-config-activation",
                        actor="alice",
                        instrument_ids=("source-0",),
                    )
                )

            control = runtime.application.executor._control
            assert control.list_instrument_sessions() == ()
            with control.read_transaction() as connection:
                assert control.list_resource_claims_in_transaction(connection) == ()
            assert provider.drivers == []


def test_exclusivity_key_survives_logical_instrument_rename(tmp_path: Path) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            first = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-before-instrument-rename",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            original_config = load_config()
            [source] = original_config.instrument_registry.instruments
            renamed_id = "renamed-source"
            registry = original_config.instrument_registry.model_copy(
                update={"instruments": [source.model_copy(update={"id": renamed_id})]}
            )
            routing = original_config.routing.model_copy(
                update={
                    "routes": [
                        route.model_copy(update={"instrument_id": renamed_id})
                        for route in original_config.routing.routes
                    ]
                }
            )
            renamed = original_config.model_copy(
                update={
                    "id": "renamed-config",
                    "system": original_config.system.model_copy(
                        update={
                            "instrument_registry": registry,
                            "routing": routing,
                        }
                    ),
                }
            )
            runtime.application.config.publish_config(
                ConfigPublishCommand(
                    operation_id="publish:renamed-config",
                    source=DirectConfigRevisionSource(config=renamed),
                    entry_id="renamed-config",
                    actor="operator",
                    expected_generation=1,
                )
            )

            [current] = daemon.list_instruments().items
            assert current.instrument_id == renamed_id
            assert current.availability == "active"
            assert current.owner_actor == "alice"
            with pytest.raises(DaemonConflictError, match="resources are busy"):
                daemon.open_instrument_session(
                    InstrumentSessionOpenCommand(
                        operation_id="open-renamed-while-owned",
                        actor="bob",
                        instrument_ids=(renamed_id,),
                    )
                )
            assert len(provider.drivers) == 1

            daemon.close_instrument_session(first.session_id)
            second = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-after-instrument-rename",
                    actor="bob",
                    instrument_ids=(renamed_id,),
                )
            )

            assert len(provider.drivers) == 2
            assert provider.drivers[0].disconnect_count == 1
            daemon.close_instrument_session(second.session_id)


@pytest.mark.parametrize(
    "instrument_update",
    [
        {"connection": VirtualInstrumentConnection(options={"resource": "alternate"})},
        {"driver_id": "tests.alternate_signal_instrument"},
    ],
    ids=("connection-options", "driver"),
)
def test_binding_identity_change_reconnects_idle_instrument(
    tmp_path: Path,
    instrument_update: dict[str, object],
) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            first = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-before-binding-change",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            daemon.close_instrument_session(first.session_id)
            [original] = provider.drivers

            config = load_config()
            [instrument] = config.instrument_registry.instruments
            updated_instrument = instrument.model_copy(update=instrument_update)
            registry = config.instrument_registry.model_copy(
                update={"instruments": [updated_instrument]}
            )
            updated = config.model_copy(
                update={
                    "id": "updated-binding",
                    "system": config.system.model_copy(
                        update={"instrument_registry": registry}
                    ),
                }
            )
            runtime.application.config.publish_config(
                ConfigPublishCommand(
                    operation_id="publish:updated-binding",
                    source=DirectConfigRevisionSource(config=updated),
                    entry_id="updated-binding",
                    actor="operator",
                    expected_generation=1,
                )
            )

            second = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-after-binding-change",
                    actor="bob",
                    instrument_ids=("source-0",),
                )
            )
            assert len(provider.drivers) == 2
            assert original.disconnect_count == 1
            daemon.close_instrument_session(second.session_id)


def test_contract_identity_change_reconnects_idle_instrument(tmp_path: Path) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            first = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-before-contract-change",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            daemon.close_instrument_session(first.session_id)
            [original] = provider.drivers

            provider._driver_type = _UpdatedContractDriver
            second = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-after-contract-change",
                    actor="bob",
                    instrument_ids=("source-0",),
                )
            )

            assert len(provider.drivers) == 2
            assert original.disconnect_count == 1
            assert second.descriptions[0].implementation_version == "v1"
            daemon.close_instrument_session(second.session_id)


def test_stale_idle_connection_is_replaced_after_resynchronization_failure(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            first = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-before-stale",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            daemon.close_instrument_session(first.session_id)

            [stale] = provider.drivers
            assert isinstance(stale, _ResyncDriver)
            stale.fail_next_read = True

            second = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-after-stale",
                    actor="bob",
                    instrument_ids=("source-0",),
                )
            )
            assert len(provider.drivers) == 2
            replacement = provider.drivers[1]
            assert isinstance(replacement, _ResyncDriver)
            assert stale.disconnect_count == 1
            assert replacement.read_count == 1
            assert daemon.list_instruments().items[0].availability == "active"
            daemon.close_instrument_session(second.session_id)


def test_shutdown_fences_an_owner_that_finishes_opening_after_the_drain_starts(
    tmp_path: Path,
) -> None:
    provider = _ShutdownRaceProvider()
    with _runtime(tmp_path, provider, config=_two_instrument_config()) as runtime:
        instruments = runtime.application.instruments
        first = instruments.open_session(
            InstrumentSessionOpenCommand(
                operation_id="open-before-shutdown",
                actor="alice",
                instrument_ids=("source-0",),
            )
        )
        control = runtime.application.executor._control
        control.start_instrument_operation(
            first.session_id,
            instrument_id="source-0",
            operation_id="pending-apply",
            kind="apply",
        )

        open_errors: list[BaseException] = []
        shutdown_errors: list[BaseException] = []

        def open_during_shutdown() -> None:
            try:
                instruments.open_session(
                    InstrumentSessionOpenCommand(
                        operation_id="open-racing-shutdown",
                        actor="bob",
                        instrument_ids=("source-1",),
                    )
                )
            except BaseException as error:
                open_errors.append(error)

        def shut_down() -> None:
            try:
                instruments.shutdown()
            except BaseException as error:
                shutdown_errors.append(error)

        opening = Thread(target=open_during_shutdown)
        opening.start()
        assert provider.read_entered.wait(timeout=3)

        shutting_down = Thread(target=shut_down)
        shutting_down.start()
        assert provider.abort_entered.wait(timeout=3)

        provider.release_read.set()
        opening.join(timeout=3)
        provider.release_abort.set()
        shutting_down.join(timeout=3)

        assert not opening.is_alive()
        assert not shutting_down.is_alive()
        assert not shutdown_errors
        assert len(open_errors) == 1
        assert isinstance(open_errors[0], BackendConflict)
        assert str(open_errors[0]) == "instrument service is shutting down"
        assert len(provider.drivers) == 2
        by_id = {driver.instrument_id: driver for driver in provider.drivers}
        assert by_id["source-0"].disconnect_count == 1
        assert by_id["source-1"].disconnect_count == 1
        sessions = control.list_instrument_sessions()
        assert not [session for session in sessions if session.state == "active"]
        assert {session.state for session in sessions} == {
            "attention_required",
            "closed",
        }


def test_shutdown_owns_a_session_already_selected_for_lease_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:
        instruments = runtime.application.instruments
        session = instruments.open_session(
            InstrumentSessionOpenCommand(
                operation_id="open-expiry-shutdown-race",
                actor="alice",
                instrument_ids=("source-0",),
            )
        )
        release_started = Event()
        drain_started = Event()
        continue_release = Event()
        release_calls = 0
        original_release = cast(
            "Callable[..., bool]",
            vars(instrument_service_module)["release_instruments"],
        )
        original_drain: Callable[..., None] = instruments._drain_shutdown

        def delayed_release(*args: object, **kwargs: object) -> bool:
            nonlocal release_calls
            release_calls += 1
            release_started.set()
            assert continue_release.wait(timeout=3)
            return original_release(*args, **kwargs)

        def observed_drain(*args: object, **kwargs: object) -> None:
            drain_started.set()
            original_drain(*args, **kwargs)

        monkeypatch.setattr(
            instrument_service_module,
            "release_instruments",
            delayed_release,
        )
        monkeypatch.setattr(instruments, "_drain_shutdown", observed_drain)
        expiry = Thread(target=lambda: instruments.expire_leases(at=session.expires_at))
        shutdown = Thread(target=instruments.shutdown)

        expiry.start()
        assert release_started.wait(timeout=3)
        shutdown.start()
        assert drain_started.wait(timeout=3)
        continue_release.set()
        expiry.join(timeout=3)
        shutdown.join(timeout=3)

        assert not expiry.is_alive()
        assert not shutdown.is_alive()
        assert release_calls == 1
        durable = runtime.application.executor._control.get_instrument_session(
            session.session_id
        )
        assert durable.state == "closed"
        assert durable.end_status == "aborted"


def test_session_heartbeat_is_independent_of_another_slow_open(
    tmp_path: Path,
) -> None:
    provider = _ShutdownRaceProvider()
    with _runtime(tmp_path, provider, config=_two_instrument_config()) as runtime:
        instruments = runtime.application.instruments
        first = instruments.open_session(
            InstrumentSessionOpenCommand(
                operation_id="open-heartbeat-owner",
                actor="alice",
                instrument_ids=("source-0",),
            )
        )
        opened: list[str] = []
        open_errors: list[BaseException] = []

        def open_second() -> None:
            try:
                session = instruments.open_session(
                    InstrumentSessionOpenCommand(
                        operation_id="open-slow-owner",
                        actor="bob",
                        instrument_ids=("source-1",),
                    )
                )
                opened.append(session.session_id)
            except BaseException as error:
                open_errors.append(error)

        opening = Thread(target=open_second)
        opening.start()
        assert provider.read_entered.wait(timeout=3)

        renewed = instruments.renew_session(first.session_id)

        provider.release_read.set()
        opening.join(timeout=3)
        assert not opening.is_alive()
        assert not open_errors
        assert len(opened) == 1
        assert renewed.expires_at > first.expires_at
        instruments.close_session(first.session_id)
        instruments.close_session(opened[0])


def test_provider_rejection_closes_the_daemon_session(tmp_path: Path) -> None:
    provider = _RejectedProvider()
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)

        with pytest.raises(DaemonConflictError, match="provider rejected"):
            daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-rejected",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )

        [session] = runtime.application.executor._control.list_instrument_sessions()
        assert session.state == "closed"
        assert session.end_status == "aborted"
        [instrument] = daemon.list_instruments().items
        assert instrument.availability == "available"
        assert provider.drivers == []


def test_notebook_open_retry_reuses_operation_after_response_loss(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(
                transport,
                drop_response_suffix="/instrument-sessions",
            )
            handle = LabClient(daemon).instruments.open(_raw_instrument("source-0"))

            state = handle._read_state()
            handle.close()

            assert state.instrument_id == "source-0"
            assert len(provider.drivers) == 1


def test_abort_retry_replays_receipt_without_repeating_driver_calls(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-abort-retry",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            first = daemon.abort_instrument_session(session.session_id)
            second = daemon.abort_instrument_session(session.session_id)

            assert second == first
            [driver] = provider.drivers
            assert driver.abort_count == 1
            assert driver.disconnect_count == 0
            assert daemon.close_instrument_session(session.session_id) == first


def test_abort_failure_faults_connection_without_repeating_abort(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_AbortFailDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            first = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-abort-failure",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )

            with pytest.raises(
                DaemonConflictError,
                match="abort was not confirmed",
            ):
                daemon.abort_instrument_session(first.session_id)

            [faulted] = provider.drivers
            assert faulted.abort_count == 1
            assert faulted.disconnect_count == 1
            daemon.resolve_instrument_session_attention(first.session_id)

            second = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-after-abort-failure",
                    actor="bob",
                    instrument_ids=("source-0",),
                )
            )
            assert len(provider.drivers) == 2
            daemon.close_instrument_session(second.session_id)


def test_notebook_close_remains_retryable_after_both_transport_attempts_fail(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(
                transport,
                drop_response_suffix="/close",
                drop_response_count=2,
            )
            handle = LabClient(daemon).instruments.open(_raw_instrument("source-0"))
            handle._read_state()

            with pytest.raises(httpx2.ReadError, match="response was lost"):
                handle.close()
            receipt = handle.close()

            assert receipt is not None
            assert receipt.status == "closed"
            [driver] = provider.drivers
            assert driver.disconnect_count == 0


def test_notebook_default_apply_retries_with_same_operation_after_response_loss(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(
                transport,
                drop_response_suffix="/state/apply",
            )
            handle = LabClient(daemon).instruments.open(_raw_instrument("source-0"))

            receipt = handle._apply(
                {_SET_FREQUENCY: Quantity(value=5.1, unit="GHz")},
            )
            handle.close()

            assert receipt.status == "applied"
            [driver] = provider.drivers
            assert len(driver.applied) == 1


def test_configured_defaults_replay_after_response_loss_avoids_hardware_io(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.1, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider, config=config) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            handle = LabClient(
                _daemon_client(
                    transport,
                    drop_response_suffix="/configured-defaults/apply",
                )
            ).instruments.open(_raw_instrument("source-0"))
            handle._observed_state()
            [driver] = provider.drivers
            assert isinstance(driver, _ResyncDriver)

            receipt = handle._apply_configured_defaults()

            assert receipt.status == "applied"
            assert driver.read_count == 3
            assert len(driver.applied) == 1
            handle.close()


def test_notebook_default_collect_retries_with_same_operation_after_response_loss(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(
                transport,
                drop_response_suffix="/collect",
            )
            handle = LabClient(daemon).instruments.open(_raw_instrument("source-0"))

            receipt = handle._collect(
                _SAMPLE_SIGNAL,
                _SAMPLE_SIGNAL.result("signal"),
            )
            handle.close()

            assert receipt.status == "collected"
            [driver] = provider.drivers
            assert len(driver.collect_requests) == 1


def test_observation_failure_aborts_session_without_quarantining(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ReadFailDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            with pytest.raises(
                DaemonConflictError,
                match="state could not be observed",
            ):
                daemon.open_instrument_session(
                    InstrumentSessionOpenCommand(
                        operation_id="open-read-failure",
                        actor="alice",
                        instrument_ids=("source-0",),
                    )
                )

            [instrument] = daemon.list_instruments().items
            [driver] = provider.drivers
            assert instrument.availability == "available"
            assert driver.disconnected


@pytest.mark.parametrize("invalid_snapshot", [False, True])
def test_explicit_observation_failure_ends_the_entire_session(
    tmp_path: Path,
    *,
    invalid_snapshot: bool,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    config = _two_instrument_config()
    with _runtime(tmp_path, provider, config=config) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            first = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id=f"open-explicit-read-failure-{invalid_snapshot}",
                    actor="alice",
                    instrument_ids=("source-0", "source-1"),
                )
            )
            drivers = {driver.instrument_id: driver for driver in provider.drivers}
            failed_driver = drivers["source-0"]
            assert isinstance(failed_driver, _ResyncDriver)
            if invalid_snapshot:
                failed_driver.return_invalid_next_read = True
            else:
                failed_driver.fail_next_read = True

            with pytest.raises(DaemonConflictError):
                daemon.read_instrument_state(first.session_id, "source-0")

            durable = runtime.application.executor._control.get_instrument_session(
                first.session_id
            )
            assert durable.state == "closed"
            assert durable.end_status == "aborted"
            assert {item.availability for item in daemon.list_instruments().items} == {
                "available"
            }
            assert all(driver.abort_count == 0 for driver in drivers.values())
            assert all(driver.disconnect_count == 1 for driver in drivers.values())
            assert daemon.close_instrument_session(first.session_id).status == "aborted"

            second = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id=f"reopen-after-read-failure-{invalid_snapshot}",
                    actor="bob",
                    instrument_ids=("source-0", "source-1"),
                )
            )
            assert len(provider.drivers) == 4
            daemon.close_instrument_session(second.session_id)


def test_explicit_observation_cleanup_failure_requires_attention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            opened = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-explicit-read-cleanup-failure",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            control = runtime.application.executor._control

            def fail_close(*_args: object, **_kwargs: object) -> Never:
                raise RuntimeError("control close failed")

            monkeypatch.setattr(control, "close_instrument_session", fail_close)
            [driver] = provider.drivers
            assert isinstance(driver, _ResyncDriver)
            driver.fail_next_read = True

            with pytest.raises(
                DaemonConflictError,
                match="observation failure could not be released",
            ):
                daemon.read_instrument_state(opened.session_id, "source-0")

            durable = control.get_instrument_session(opened.session_id)
            assert durable.state == "attention_required"
            assert (
                durable.attention_reason
                == "instrument_session_observation_cleanup_failed"
            )
            assert driver.abort_count == 0
            assert driver.disconnect_count == 1
            [instrument] = daemon.list_instruments().items
            assert instrument.availability == "quarantined"


def test_acquisition_cleanup_failure_quarantines_the_durable_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _TrackingProvider(_ReadFailDriver)
    with _runtime(tmp_path, provider) as runtime:
        control = runtime.application.executor._control

        def fail_close(*_args: object, **_kwargs: object) -> Never:
            raise RuntimeError("control close failed")

        monkeypatch.setattr(control, "close_instrument_session", fail_close)
        with pytest.raises(
            BackendConflict,
            match="acquisition could not be released",
        ):
            runtime.application.instruments.open_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-cleanup-failure",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )

        [session] = control.list_instrument_sessions()
        assert session.state == "attention_required"
        assert session.attention_reason == "instrument_session_open_cleanup_failed"
        [instrument] = runtime.application.instruments.list_instruments().items
        assert instrument.availability == "quarantined"


def test_direct_session_observes_without_applying_default_state(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.0, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider, config=config) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            handle = LabClient(_daemon_client(transport)).instruments.open(
                _raw_instrument("source-0"),
            )
            handle._observed_state()

            [driver] = provider.drivers
            assert driver.applied == []
            handle.close()


def test_missing_configured_defaults_reject_without_hardware_io(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-without-configured-defaults",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            assert session.configured_default_instrument_ids == ()
            command = InstrumentConfiguredDefaultsApplyCommand(
                operation_id="apply-missing-configured-defaults"
            )

            first = daemon.apply_instrument_configured_defaults(
                session.session_id,
                "source-0",
                command,
            )
            replay = daemon.apply_instrument_configured_defaults(
                session.session_id,
                "source-0",
                command,
            )

            assert replay == first
            assert first.status == "rejected"
            assert first.problems[0].code == "instrument_configured_defaults_missing"
            [driver] = provider.drivers
            assert isinstance(driver, _ResyncDriver)
            assert driver.read_count == 1
            assert driver.applied == []
            assert (
                runtime.application.executor._control.get_instrument_session(
                    session.session_id
                ).state
                == "active"
            )
            daemon.close_instrument_session(session.session_id)


def test_configured_defaults_read_fresh_state_and_write_only_pending_values(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.1, unit="GHz")),
        ),
        _setting(
            interface_id="test.set_gain/v1",
            property_id="gain",
            value=StateValue(1.0),
        ),
    )
    with _runtime(tmp_path, provider, config=config) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            handle = LabClient(_daemon_client(transport)).instruments.open(
                _raw_instrument("source-0"),
            )
            handle._observed_state()
            [driver] = provider.drivers
            assert isinstance(driver, _ResyncDriver)
            driver.change_from_front_panel(5.1)

            receipt = handle._apply_configured_defaults()

            assert receipt.status == "applied"
            assert driver.read_count == 3
            [request] = driver.applied
            assert [
                (target.interface_id, target.property_id, value)
                for target, value in request.values.items()
                if isinstance(target, PropertyRef)
            ] == [("test.set_gain/v1", "gain", 1.0)]
            handle.close()


def test_configured_defaults_skip_write_when_fresh_state_already_matches(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.1, unit="GHz")),
        ),
        _setting(
            interface_id="test.set_gain/v1",
            property_id="gain",
            value=StateValue(0.0),
        ),
    )
    with _runtime(tmp_path, provider, config=config) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            handle = LabClient(_daemon_client(transport)).instruments.open(
                _raw_instrument("source-0"),
            )
            handle._observed_state()
            [driver] = provider.drivers
            assert isinstance(driver, _ResyncDriver)
            driver.change_from_front_panel(5.1)

            receipt = handle._apply_configured_defaults()

            assert receipt.status == "unchanged"
            assert driver.read_count == 2
            assert driver.applied == []
            handle.close()


def test_explicit_defaults_use_session_pinned_preserve_config(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    pinned_config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.0, unit="GHz")),
        ),
        run_start="preserve",
    )
    with _runtime(tmp_path, provider, config=pinned_config) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            active = runtime.application.config.get_active_config()
            handle = LabClient(_daemon_client(transport)).instruments.open(
                _raw_instrument("source-0"),
            )
            handle._observed_state()
            later_config = _config_with_default_state(
                _setting(
                    interface_id="test.set_frequency/v1",
                    property_id="frequency",
                    value=StateValue(Quantity(value=6.0, unit="GHz")),
                )
            ).model_copy(update={"id": "later-defaults"})
            runtime.application.config.publish_config(
                ConfigPublishCommand(
                    operation_id="publish:later-defaults",
                    source=DirectConfigRevisionSource(config=later_config),
                    entry_id="later-defaults",
                    actor="operator",
                    expected_generation=active.activation.generation,
                )
            )

            receipt = handle._apply_configured_defaults()

            assert receipt.status == "applied"
            assert receipt.config_entry_id == active.entry.id
            [driver] = provider.drivers
            [request] = driver.applied
            assert next(iter(request.values.values())) == Quantity(
                value=5.0,
                unit="GHz",
            )
            handle.close()


def test_rejected_configured_defaults_remain_active_and_replayable(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_NotAppliedDriver)
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.0, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider, config=config) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-rejected-configured-defaults",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            command = InstrumentConfiguredDefaultsApplyCommand(
                operation_id="reject-configured-defaults"
            )
            first = daemon.apply_instrument_configured_defaults(
                session.session_id,
                "source-0",
                command,
            )
            replay = daemon.apply_instrument_configured_defaults(
                session.session_id,
                "source-0",
                command,
            )

            assert replay == first
            assert first.status == "rejected"
            assert first.problems[0].code == "driver_rejected_apply"
            [driver] = provider.drivers
            assert isinstance(driver, _NotAppliedDriver)
            assert driver.read_count == 2
            assert len(driver.applied) == 1
            durable = runtime.application.executor._control.get_instrument_session(
                session.session_id
            )
            assert durable.state == "active"
            [instrument] = daemon.list_instruments().items
            assert instrument.availability == "active"
            daemon.close_instrument_session(session.session_id)


@pytest.mark.parametrize(
    ("driver_type", "attention_reason", "read_count"),
    [
        (
            _UnknownApplyDriver,
            "instrument_configured_defaults_receipt_unknown",
            2,
        ),
        (
            _NonConvergingApplyDriver,
            "instrument_configured_defaults_state_unknown",
            3,
        ),
    ],
)
def test_indeterminate_configured_defaults_quarantine_the_session(
    tmp_path: Path,
    *,
    driver_type: type[_TrackingDriver],
    attention_reason: str,
    read_count: int,
) -> None:
    provider = _TrackingProvider(driver_type)
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.0, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider, config=config) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            handle = LabClient(daemon).instruments.open(_raw_instrument("source-0"))
            handle._observed_state()
            [owned] = daemon.list_instruments().items
            assert owned.owner_id is not None
            session_id = owned.owner_id

            with pytest.raises(DaemonConflictError):
                handle._apply_configured_defaults()

            durable = runtime.application.executor._control.get_instrument_session(
                session_id
            )
            assert durable.state == "attention_required"
            assert durable.attention_reason == attention_reason
            [driver] = provider.drivers
            assert isinstance(driver, _ResyncDriver)
            assert driver.read_count == read_count
            assert len(driver.applied) == 1
            assert driver.abort_count == 1
            assert driver.disconnect_count == 1
            [instrument] = daemon.list_instruments().items
            assert instrument.availability == "quarantined"


def test_configured_defaults_read_failure_cleanly_closes_the_session(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_ResyncDriver)
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.0, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider, config=config) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            handle = LabClient(daemon).instruments.open(_raw_instrument("source-0"))
            handle._observed_state()
            [owned] = daemon.list_instruments().items
            assert owned.owner_id is not None
            session_id = owned.owner_id
            [driver] = provider.drivers
            assert isinstance(driver, _ResyncDriver)
            driver.fail_next_read = True

            with pytest.raises(
                DaemonConflictError,
                match="state read failed",
            ):
                handle._apply_configured_defaults()

            durable = runtime.application.executor._control.get_instrument_session(
                session_id
            )
            assert durable.state == "closed"
            assert durable.end_status == "aborted"
            assert driver.applied == []
            assert driver.abort_count == 0
            assert driver.disconnect_count == 1
            [instrument] = daemon.list_instruments().items
            assert instrument.availability == "available"
            end_receipt = handle.close()
            assert end_receipt is not None
            assert end_receipt.status == "aborted"


def test_invalid_collect_receipt_is_deduplicated_without_quarantining(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider(_InvalidCollectDriver)
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-invalid-collect",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            intent = InteractiveCollectIntent(
                command_id="collect-invalid",
                instrument_id="source-0",
                interface_id="test.scalar_signal/v1",
                acquisition_id="sample",
                result_ids=["signal"],
            )

            for _attempt in range(2):
                with pytest.raises(
                    DaemonConflictError,
                    match="unit_mismatch",
                ):
                    daemon.collect_instrument(
                        session.session_id,
                        "source-0",
                        intent,
                    )
            with pytest.raises(
                DaemonConflictError,
                match="different collect content",
            ):
                daemon.collect_instrument(
                    session.session_id,
                    "source-0",
                    intent.model_copy(update={"acquisition_id": "other"}),
                )

            [driver] = provider.drivers
            [instrument] = daemon.list_instruments().items
            assert len(driver.collect_requests) == 1
            assert instrument.availability == "active"
            daemon.close_instrument_session(session.session_id)


def test_provider_instance_and_virtual_state_survive_across_sessions(
    tmp_path: Path,
) -> None:
    provider = _StatefulProvider()
    with (
        LocalDaemonRuntime(
            tmp_path,
            bootstrap_config=load_config(),
            instrument_endpoint=LocalInstrumentBackendEndpoint(
                InstrumentBackend(
                    provider=provider,
                    driver_catalog=DriverCatalog(provider_id=provider.provider_id),
                )
            ),
        ) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        first = daemon.open_instrument_session(
            InstrumentSessionOpenCommand(
                operation_id="open-stateful-1",
                actor="alice",
                instrument_ids=("source-0",),
            )
        )
        daemon.apply_instrument_state(
            first.session_id,
            "source-0",
            _apply_command(value=5.1),
        )
        daemon.close_instrument_session(first.session_id)

        second = daemon.open_instrument_session(
            InstrumentSessionOpenCommand(
                operation_id="open-stateful-2",
                actor="alice",
                instrument_ids=("source-0",),
            )
        )
        [state] = second.observed_state
        daemon.close_instrument_session(second.session_id)

    property_state = next(
        item
        for item in state.observations
        if item.target.kind == "interface"
        and item.target.interface_id == "test.set_frequency/v1"
    )
    assert property_state.value == StateValue(Quantity(value=5.1, unit="GHz"))


def test_contract_catalog_resolves_the_requested_non_active_config(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    requested = _two_instrument_config()
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        catalog = _daemon_client(transport).resolve_instrument_contracts(requested)

    assert catalog.config_content_hash == config_content_hash(requested)
    assert tuple(item.instrument_id for item in catalog.instruments) == (
        "source-0",
        "source-1",
    )
    assert provider.described_bindings[-1] == instrument_bindings(requested)


def test_contract_catalog_is_empty_without_an_instrument_backend(
    tmp_path: Path,
) -> None:
    config = load_config()
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        catalog = _daemon_client(transport).resolve_instrument_contracts(config)

    assert catalog.config_content_hash == config_content_hash(config)
    assert catalog.provider_id is None
    assert catalog.instruments == ()
    assert catalog.problems == ()


def test_provider_problems_are_scoped_without_polluting_healthy_views(
    tmp_path: Path,
) -> None:
    config = _two_instrument_config()
    provider = _ProblemProvider()
    with _runtime(tmp_path, provider, config=config) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            instruments = _daemon_client(transport).list_instruments()

    by_id = {item.instrument_id: item for item in instruments.items}
    assert [item.code for item in by_id["source-0"].problems] == ["source_zero_problem"]
    assert [item.code for item in by_id["source-1"].problems] == ["source_one_problem"]
    assert [item.code for item in instruments.problems] == ["provider_global_problem"]


def test_direct_session_ignores_unrelated_instrument_description_problem(
    tmp_path: Path,
) -> None:
    provider = _ScopedProblemProvider()
    with (
        _runtime(tmp_path, provider, config=_two_instrument_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        session = daemon.open_instrument_session(
            InstrumentSessionOpenCommand(
                operation_id="open-healthy-source",
                actor="alice",
                instrument_ids=("source-1",),
            )
        )

        assert session.instrument_ids == ("source-1",)
        daemon.close_instrument_session(session.session_id)


def test_run_and_interactive_session_compete_for_the_same_resource(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    config = load_config()
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-exclusive",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )
            admission = daemon.submit_run(_submission(config))

            with pytest.raises(DaemonConflictError, match="resources are busy"):
                daemon.start_executor(
                    admission.run_id,
                    ExecutorStartRequest(executor_id="notebook"),
                )

            daemon.close_instrument_session(session.session_id)
            executor = daemon.start_executor(
                admission.run_id,
                ExecutorStartRequest(executor_id="notebook"),
            )
            assert executor.run_id == admission.run_id

            with pytest.raises(DaemonConflictError, match="resources are busy"):
                daemon.open_instrument_session(
                    InstrumentSessionOpenCommand(
                        operation_id="open-while-run",
                        actor="bob",
                        instrument_ids=("source-0",),
                    )
                )


def test_run_admission_rejects_alternate_key_for_active_connection(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    config = load_config()
    [source] = config.instrument_registry.instruments
    changed_registry = config.instrument_registry.model_copy(
        update={
            "instruments": [
                source.model_copy(update={"exclusivity_key": "alternate-source"})
            ]
        }
    )
    changed = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={"instrument_registry": changed_registry}
            )
        }
    )
    with _runtime(tmp_path, provider) as runtime:  # noqa: SIM117
        with TestClient(runtime.app()) as transport:
            daemon = _daemon_client(transport)
            session = daemon.open_instrument_session(
                InstrumentSessionOpenCommand(
                    operation_id="open-authoritative-key",
                    actor="alice",
                    instrument_ids=("source-0",),
                )
            )

            with pytest.raises(
                DaemonConflictError,
                match="instrument inventory differs",
            ):
                daemon.submit_run(_submission(changed))

            admission = daemon.submit_run(_submission(config))
            with pytest.raises(DaemonConflictError, match="resources are busy"):
                daemon.start_executor(
                    admission.run_id,
                    ExecutorStartRequest(executor_id="notebook"),
                )
            assert len(provider.drivers) == 1
            daemon.close_instrument_session(session.session_id)


def _runtime(
    root: Path,
    provider: InstrumentProvider,
    *,
    config: ConfigProfileSnapshot | None = None,
    driver_catalog: DriverCatalog | None = None,
    session_lease_ttl: timedelta = _SESSION_LEASE_TTL,
) -> LocalDaemonRuntime:
    return LocalDaemonRuntime(
        root,
        bootstrap_config=config if config is not None else load_config(),
        instrument_endpoint=LocalInstrumentBackendEndpoint(
            InstrumentBackend(
                provider=provider,
                driver_catalog=(
                    DriverCatalog(provider_id=provider.provider_id)
                    if driver_catalog is None
                    else driver_catalog
                ),
                payload_codecs=json_payload_codecs("pulse_program"),
            )
        ),
        instrument_session_lease_ttl=session_lease_ttl,
    )


def _daemon_client(
    transport: TestClient,
    *,
    drop_response_suffix: str | None = None,
    drop_response_count: int = 1,
    on_response_drop: Callable[[], None] | None = None,
) -> DaemonClient:
    responses_dropped = 0

    def send(request: httpx2.Request) -> httpx2.Response:
        nonlocal responses_dropped
        response = transport.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        translated = httpx2.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )
        if (
            drop_response_suffix is not None
            and request.url.path.endswith(drop_response_suffix)
            and responses_dropped < drop_response_count
        ):
            responses_dropped += 1
            if on_response_drop is not None:
                on_response_drop()
            raise httpx2.ReadError(
                "response was lost",
                request=request,
            )
        return translated

    return DaemonClient(
        "http://testserver",
        transport=httpx2.MockTransport(send),
    )


def _selected_ids(context: InstrumentProviderContext) -> Sequence[str]:
    return tuple(item.id for item in context.bindings)


def _apply_command(*, value: float) -> InstrumentStateCommand:
    return InstrumentStateCommand(
        command_id="apply-1",
        instrument_id="source-0",
        assignments=[
            InstrumentStateAssignment(
                resource_id="source-0",
                target=state_member_target(_SET_FREQUENCY),
                value=StateValue(Quantity(value=value, unit="GHz")),
            )
        ],
    )


def _variant_collect_intent(
    *,
    command_id: str,
    result_ids: tuple[str, ...] = (),
) -> InteractiveCollectIntent:
    return InteractiveCollectIntent(
        command_id=command_id,
        instrument_id="source-0",
        interface_id="test.dc/v1",
        acquisition_id="measure",
        result_ids=list(result_ids),
    )


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


def _config_with_default_state(
    *properties: InstrumentStateSetting,
    run_start: InstrumentRunStartPolicy = "apply_default_state",
) -> ConfigProfileSnapshot:
    config = load_config()
    [instrument] = config.instrument_registry.instruments
    configured = instrument.model_copy(
        update={
            "run_start": run_start,
            "default_state": list(properties),
        }
    )
    registry = config.instrument_registry.model_copy(
        update={"instruments": [configured]}
    )
    return config.model_copy(
        update={
            "system": config.system.model_copy(update={"instrument_registry": registry})
        }
    )


def _config_with_private_instrument_settings() -> ConfigProfileSnapshot:
    config = _two_instrument_config()
    source, virtual = config.instrument_registry.instruments
    configured = source.model_copy(
        update={
            "connection": TcpipSocketInstrumentConnection(
                host="instrument.example",
                port=5025,
                timeout_seconds=17.0,
                options={"api_token": "private-token"},
            ),
            "default_state": [
                _setting(
                    interface_id="test.set_frequency/v1",
                    property_id="frequency",
                    value=StateValue(Quantity(value=5.0, unit="GHz")),
                )
            ],
            "run_start": "apply_default_state",
        }
    )
    registry = config.instrument_registry.model_copy(
        update={"instruments": [configured, virtual]}
    )
    return config.model_copy(
        update={
            "system": config.system.model_copy(update={"instrument_registry": registry})
        }
    )


def _submission(config: ConfigProfileSnapshot) -> RunSubmission:
    [instrument] = config.instrument_registry.instruments
    return RunSubmission(
        submission_id="interactive-exclusion",
        config=config,
        request=RunRequest(experiment_id="scratch"),
        plan=RunPlanSummary(
            experiment_id="scratch",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=1,
            initial_point_count=1,
            point_limit=1,
            run_resource_requirements=(
                RunResourceRequirement(
                    kind="instrument",
                    id=instrument.id,
                ),
            ),
        ),
    )


def _raw_instrument(
    instrument_id: str,
) -> InstrumentRef[InstrumentSessionHandle]:
    return instrument(
        instrument_id,
        _raw_session,
    )


def _raw_session(
    session: InstrumentClientChannel,
    instrument_id: str,
) -> InstrumentSessionHandle:
    del instrument_id
    return session.raw_session


def test_explicit_release_disconnects_idle_connection_and_next_session_reconnects(
    tmp_path: Path,
) -> None:
    provider = _TrackingProvider()
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        lab = LabClient(_daemon_client(transport), operator="alice")
        target = _raw_instrument("source-0")
        with lab.instruments.open(target):
            with pytest.raises(DaemonConflictError, match="idle devices"):
                lab.instruments.release(target)
            [first] = provider.drivers
            assert first.disconnect_count == 0
        receipt = lab.instruments.release(target)
        assert receipt.instrument_ids == ("source-0",)
        assert first.disconnect_count == 1
        # An already released device remains released without creating a connection.
        lab.instruments.release("source-0")
        assert provider.drivers == [first]
        with lab.instruments.open(target):
            assert len(provider.drivers) == 2
            assert provider.drivers[1] is not first
            assert not provider.drivers[1].disconnected


type _WriteOnlyReply = Literal[
    "confirmed",
    "missing",
    "wrong_value",
    "wrong_unit",
    "unsupported",
    "queried",
    "rejected",
    "unknown",
]


class _WriteOnlyConfirmationDriver(_TrackingDriver):
    """Reference driver with an explicit response, never inferred by the host."""

    def __init__(self, instrument_id: str) -> None:
        super().__init__(instrument_id)
        self.reply: _WriteOnlyReply = "confirmed"

    @override
    def describe(self) -> InstrumentDescription:
        description = super().describe()
        description.interfaces[0] = interface(
            _SET_FREQUENCY.interface_id,
            properties=[
                quantity_property(
                    "frequency",
                    unit="GHz",
                    access="write_only",
                )
            ],
        )
        return description

    @override
    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        # The device cannot query this property, even after a successful write.
        return super().read_state(
            DriverStateReadRequest(
                targets=request.targets - {_SET_FREQUENCY},
            )
        )

    @override
    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.applied.append(request)
        if self.reply in ("rejected", "unknown"):
            problems = (
                problem(
                    "reference_write_failed",
                    "reference write did not confirm state",
                    phase=ProblemPhase.EXECUTION,
                ),
            )
            return (
                DriverRejected(problems=problems)
                if self.reply == "rejected"
                else DriverUnknown(problems=problems)
            )
        if self.reply == "missing":
            return DriverSuccess(None)
        return DriverSuccess(
            DriverStateReadback(
                observations=(
                    DriverStateObservation(
                        target=(
                            _SET_FREQUENCY
                            if self.reply != "unsupported"
                            else InterfaceRef(_SET_FREQUENCY.interface_id).property(
                                "missing"
                            )
                        ),
                        value=Quantity(
                            5.0 if self.reply == "wrong_value" else 5.1,
                            "K" if self.reply == "wrong_unit" else "GHz",
                        ),
                        source="hardware_query"
                        if self.reply == "queried"
                        else "command_confirmed",
                    ),
                )
            )
        )


def test_write_only_apply_accepts_explicit_command_confirmation(tmp_path: Path) -> None:
    provider = _TrackingProvider(_WriteOnlyConfirmationDriver)
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        lab = LabClient(_daemon_client(transport))
        with lab.instruments.open(_raw_instrument("source-0")) as handle:
            receipt = handle._apply({_SET_FREQUENCY: Quantity(5.1, "GHz")})
            assert receipt.status == "applied"
            assert receipt.readback is not None
            [observation] = receipt.readback.observations
            assert observation.source == "command_confirmed"
            assert observation.value.root == Quantity(5.1, "GHz")
            [driver] = provider.drivers
            assert len(driver.applied) == 1
            assert not driver.read_state(
                DriverStateReadRequest(
                    targets=frozenset({_SET_FREQUENCY}),
                )
            ).observations


@pytest.mark.parametrize(
    "reply",
    [
        "missing",
        "wrong_value",
        "wrong_unit",
        "unsupported",
        "queried",
        "unknown",
    ],
)
def test_write_only_unconfirmed_apply_is_quarantined_without_retry(
    tmp_path: Path,
    reply: _WriteOnlyReply,
) -> None:
    provider = _TrackingProvider(_WriteOnlyConfirmationDriver)
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        daemon = _daemon_client(transport)
        session = daemon.open_instrument_session(
            InstrumentSessionOpenCommand(
                operation_id="open-write-only",
                actor="alice",
                instrument_ids=("source-0",),
            )
        )
        [driver] = provider.drivers
        assert isinstance(driver, _WriteOnlyConfirmationDriver)
        driver.reply = reply
        if reply == "unknown":
            receipt = daemon.apply_instrument_state(
                session.session_id,
                "source-0",
                _apply_command(value=5.1),
            )
            assert receipt.status == "unknown"
            assert receipt.readback is None
        else:
            with pytest.raises(DaemonConflictError):
                daemon.apply_instrument_state(
                    session.session_id,
                    "source-0",
                    _apply_command(value=5.1),
                )
        assert len(driver.applied) == 1
        assert driver.disconnect_count == 1
        [instrument_view] = daemon.list_instruments().items
        assert instrument_view.availability == "quarantined"


def test_write_only_rejected_write_has_no_confirmation_or_retry(tmp_path: Path) -> None:
    provider = _TrackingProvider(_WriteOnlyConfirmationDriver)
    with (
        _runtime(tmp_path, provider) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        lab = LabClient(_daemon_client(transport))
        with lab.instruments.open(_raw_instrument("source-0")) as handle:
            [driver] = provider.drivers
            assert isinstance(driver, _WriteOnlyConfirmationDriver)
            driver.reply = "rejected"
            receipt = handle._apply({_SET_FREQUENCY: Quantity(5.1, "GHz")})
            assert receipt.status == "not_applied"
            assert receipt.readback is None
            assert len(driver.applied) == 1
            assert driver.disconnect_count == 0
