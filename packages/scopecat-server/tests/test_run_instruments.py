from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Literal, override

import pytest
from scopecat.config.candidates import (
    CandidateConfig,
    resolve_candidate_config_from_snapshot,
)
from scopecat.config.changes import parameter_change_proposal_from_updates
from scopecat.config.parameters import replace_scalar_parameter
from scopecat.control.models import RunPlanSummary, RunResourceRequirement
from scopecat.daemon.wire import (
    AnalysisParameterProposalOutputPayload,
    AnalysisSaveCommand,
    ExecutorStartRequest,
    InstrumentReleaseCommand,
    RunCoverageAdvanceCommand,
    RunHardwareBatchCommand,
    RunHardwareFinishCommand,
    RunInstrumentProvisionCommand,
    RunSubmission,
    TerminalRunCommitCommand,
)
from scopecat.kernel.problems import (
    ModelLocation,
    ProblemPhase,
    RuntimeLocation,
    model_location,
    problem,
)
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.planning.provider_validation import instrument_contract_fingerprint
from scopecat.records.config import (
    ConfigProfileSnapshot,
    InstrumentFailureAction,
    InstrumentRunStartPolicy,
    InstrumentSafeOperation,
    InstrumentSuccessAction,
    config_content_hash,
)
from scopecat.records.content import CommandPayload, command_payload_from_bytes
from scopecat.records.instrument import (
    InstrumentStateSetting,
    state_member_target,
    state_setting,
)
from scopecat.records.measurement import (
    MeasurementAcquisitionValue,
    MeasurementArray,
    MeasurementScalar,
)
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ConfigRegistryRunConfigSource,
    RunConfigSource,
)
from scopecat.records.run_request import RunRequest
from scopecat.sdk.instruments import (
    AcquisitionResultRef,
    DriverAcquisition,
    DriverAcquisitionPlan,
    DriverCatalog,
    DriverOperation,
    DriverOutcome,
    DriverPayload,
    DriverReadback,
    DriverRejected,
    DriverStatePatch,
    DriverStateReadback,
    DriverStateReadRequest,
    DriverSuccess,
    DriverUnknown,
    InstrumentBackend,
    InstrumentConnectionContext,
    InstrumentDescription,
    InstrumentProviderContext,
    InstrumentProviderDescription,
    InterfaceRef,
    PropertyRef,
    acquisition,
    acquisition_axis,
    acquisition_precondition,
    acquisition_result,
    bool_property,
    enum_property,
    float_property,
    int_property,
    interface,
    operation,
    state_readback,
    string_property,
)
from scopecat.sdk.instruments.commands import (
    CollectAxisRequest,
    CollectResultRequest,
    InstrumentOperationArgument,
    InstrumentStateAssignment,
)
from scopecat.sdk.instruments.execution import (
    RunHardwareApply,
    RunHardwareBatch,
    RunHardwareCollect,
    RunHardwareCollectBinding,
    RunHardwareInvoke,
)
from scopecat_testkit.instrument_drivers import SignalInstrumentDriver, load_config
from scopecat_testkit.payload_codecs import json_payload_codecs

from scopecat_server import LocalDaemonRuntime
from scopecat_server.errors import BackendConflict
from scopecat_server.instruments.backend import LocalInstrumentBackendEndpoint
from scopecat_server.instruments.service import InstrumentService

type _FailAction = (
    Literal[
        "apply",
        "reject_apply",
        "invoke",
        "reject_invoke",
        "unknown_collect_receipt",
        "abort",
        "disconnect",
    ]
    | None
)


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


_DC = InterfaceRef("test.dc/v1")
_DC_MODE = _DC.property("mode")
_DC_OUTPUT_ENABLED = InterfaceRef("test.dc/v1").property("output_enabled")
_DC_VOLTAGE_LEVEL = InterfaceRef("test.dc/v1").property("voltage_level")
_DC_CURRENT_LEVEL = InterfaceRef("test.dc/v1").property("current_level")
_SWEEP_POINTS = InterfaceRef("test.sweep/v1").property("points")


class _Driver(SignalInstrumentDriver):
    def __init__(
        self,
        instrument_id: str,
        *,
        fail_action: _FailAction = None,
        apply_barrier: Barrier | None = None,
    ) -> None:
        super().__init__(instrument_id=instrument_id)
        self.fail_action: _FailAction = fail_action
        self.apply_barrier = apply_barrier
        self.read_count = 0
        self.apply_call_count = 0
        self.abort_count = 0
        self.disconnect_count = 0

    @override
    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        self.read_count += 1
        return super().read_state(request)

    @override
    def apply_state(self, request: DriverStatePatch):  # type: ignore[no-untyped-def]
        self.apply_call_count += 1
        if self.apply_barrier is not None:
            self.apply_barrier.wait(timeout=2)
        if self.fail_action == "apply":
            raise RuntimeError("apply outcome lost")
        if self.fail_action == "reject_apply":
            return DriverRejected(
                problems=(
                    problem(
                        "test_apply_rejected",
                        "test driver rejected state",
                        phase=ProblemPhase.EXECUTION,
                    ),
                ),
            )
        return super().apply_state(request)

    @override
    def invoke(
        self,
        request: DriverOperation,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.invoked.append(request)
        if self.fail_action == "invoke":
            raise RuntimeError("invoke outcome lost")
        return DriverSuccess(None)

    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        if self.fail_action == "unknown_collect_receipt":
            self.collect_requests.append(request)
            return DriverUnknown(
                problems=(
                    problem(
                        "test_collect_receipt_unknown",
                        "test driver lost collection confirmation",
                        phase=ProblemPhase.EXECUTION,
                        location=model_location(
                            "driver_acquisition",
                            "results",
                        ),
                    ),
                ),
            )
        return super().collect(request)

    @override
    def abort(self) -> None:
        self.abort_count += 1
        if self.fail_action == "abort":
            raise RuntimeError("abort failed")

    @override
    def disconnect(self) -> None:
        self.disconnect_count += 1
        if self.fail_action == "disconnect":
            raise RuntimeError("disconnect failed")


class _PreparingDriver(_Driver):
    def __init__(
        self,
        instrument_id: str,
        *,
        fail_action: _FailAction = None,
        apply_barrier: Barrier | None = None,
    ) -> None:
        super().__init__(
            instrument_id,
            fail_action=fail_action,
            apply_barrier=apply_barrier,
        )
        self.events: list[str] = []
        self.acquisition_plans: list[DriverAcquisitionPlan] = []

    def prepare_acquisitions(
        self,
        plan: DriverAcquisitionPlan,
    ) -> DriverOutcome[None]:
        self.events.append("prepare")
        self.acquisition_plans.append(plan)
        return DriverSuccess(None)

    @override
    def invoke(
        self,
        request: DriverOperation,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.events.append("invoke")
        return super().invoke(request)

    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        self.events.append("collect")
        return super().collect(request)


class _StateEvidenceDriver(_Driver):
    @override
    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        outcome = super().apply_state(request)
        assert isinstance(outcome, DriverSuccess)
        return DriverSuccess(
            outcome.value,
            metadata={
                "hold_mode": "end_with_keep",
                "analog_readback": False,
            },
        )


class _RejectingCollectEvidenceDriver(_StateEvidenceDriver):
    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        self.collect_requests.append(request)
        return DriverRejected(
            problems=(
                problem(
                    "test_collect_rejected_after_apply",
                    "test driver rejected collection after applying state",
                    phase=ProblemPhase.EXECUTION,
                ),
            )
        )


class _SafeOperationDriver(_Driver):
    @override
    def describe(self) -> InstrumentDescription:
        description = super().describe()
        return description.model_copy(
            update={
                "interfaces": [
                    *description.interfaces,
                    interface(
                        "test.safe_state/v1",
                        operations=[operation("enter")],
                    ),
                ]
            }
        )

    @override
    def invoke(
        self,
        request: DriverOperation,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.invoked.append(request)
        if self.fail_action == "invoke":
            raise RuntimeError("invoke outcome lost")
        if self.fail_action == "reject_invoke":
            return DriverRejected(
                problems=(
                    problem(
                        "test_safe_operation_rejected",
                        "test driver rejected safe operation",
                        phase=ProblemPhase.EXECUTION,
                    ),
                )
            )
        return DriverSuccess(None, metadata={"device_status": "safe"})


class _VariantDriver(_Driver):
    require_output_for_collect = False

    def __init__(
        self,
        instrument_id: str,
        *,
        fail_action: _FailAction = None,
        apply_barrier: Barrier | None = None,
    ) -> None:
        super().__init__(
            instrument_id,
            fail_action=fail_action,
            apply_barrier=apply_barrier,
        )
        self.mode = "voltage"
        self.voltage_level = 0.1
        self.current_level = 0.01
        self.output_enabled = False

    @override
    def describe(self) -> InstrumentDescription:
        return InstrumentDescription(
            instrument_id=self.instrument_id,
            implementation_id=self.implementation_id,
            implementation_version=self.implementation_version,
            interfaces=[
                interface(
                    "test.dc/v1",
                    properties=[
                        enum_property("mode", choices=("voltage", "current")),
                        bool_property("output_enabled"),
                        float_property("voltage_level"),
                        float_property("current_level"),
                    ],
                    acquisitions=[
                        acquisition(
                            "measure",
                            preconditions=(
                                (
                                    acquisition_precondition(
                                        _DC_OUTPUT_ENABLED,
                                        value=True,
                                        unavailable_reason="Output is disabled.",
                                    ),
                                )
                                if self.require_output_for_collect
                                else ()
                            ),
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
                )
            ],
        )

    @override
    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        self.read_count += 1
        return state_readback(
            request,
            {
                _DC_MODE: self.mode,
                _DC_VOLTAGE_LEVEL: self.voltage_level,
                _DC_CURRENT_LEVEL: self.current_level,
                _DC_OUTPUT_ENABLED: self.output_enabled,
            },
        )

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
            elif target.property_id == "output_enabled":
                assert isinstance(value, bool)
                self.output_enabled = value
        return DriverSuccess(None)

    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        self.collect_requests.append(request)
        if self.require_output_for_collect and not self.output_enabled:
            return DriverRejected(
                problems=(
                    problem(
                        "test_acquisition_output_disabled",
                        "Output is disabled.",
                        phase=ProblemPhase.EXECUTION,
                    ),
                ),
            )
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
                        location=model_location(
                            "instrument_state",
                            "test.dc/v1",
                            "mode",
                        ),
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


class _PreconditionVariantDriver(_VariantDriver):
    require_output_for_collect = True


class _StateSizedAxisDriver(_Driver):
    def __init__(
        self,
        instrument_id: str,
        *,
        fail_action: _FailAction = None,
        apply_barrier: Barrier | None = None,
    ) -> None:
        super().__init__(
            instrument_id,
            fail_action=fail_action,
            apply_barrier=apply_barrier,
        )
        self._state = {("test.sweep/v1", "points"): 3}

    @override
    def describe(self) -> InstrumentDescription:
        return InstrumentDescription(
            instrument_id=self.instrument_id,
            implementation_id=self.implementation_id,
            implementation_version=self.implementation_version,
            interfaces=[
                interface(
                    "test.sweep/v1",
                    properties=[int_property("points", minimum=1)],
                    acquisitions=[
                        acquisition(
                            "sample",
                            results=[
                                acquisition_result(
                                    "trace",
                                    unit="V",
                                    axes=[
                                        acquisition_axis(
                                            "sample",
                                            size=_SWEEP_POINTS,
                                        )
                                    ],
                                )
                            ],
                        )
                    ],
                )
            ],
        )

    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        self.collect_requests.append(request)
        points = self._state[("test.sweep/v1", "points")]
        assert type(points) is int
        return DriverSuccess(
            DriverReadback(
                values={
                    result: MeasurementArray.create(
                        values=tuple(float(index) for index in range(points)),
                        unit="V",
                    )
                    for result in request.results
                }
            ),
        )


class _NonConvergingDriver(_Driver):
    @override
    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.applied.append(request)
        return DriverSuccess(None)


class _EquivalentQuantityDriver(_Driver):
    def __init__(
        self,
        instrument_id: str,
        *,
        fail_action: _FailAction = None,
        apply_barrier: Barrier | None = None,
    ) -> None:
        super().__init__(
            instrument_id,
            fail_action=fail_action,
            apply_barrier=apply_barrier,
        )
        self._state[("test.set_frequency/v1", "frequency")] = Quantity(
            value=1.0, unit="GHz"
        )


class _RejectNextApplyDriver(_Driver):
    @override
    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        reject = self.fail_action == "reject_apply"
        outcome = super().apply_state(request)
        if reject:
            self.fail_action = None
        return outcome


class _ReadOnlyStateDriver(_Driver):
    def __init__(
        self,
        instrument_id: str,
        *,
        fail_action: _FailAction = None,
        apply_barrier: Barrier | None = None,
    ) -> None:
        super().__init__(
            instrument_id,
            fail_action=fail_action,
            apply_barrier=apply_barrier,
        )
        self._state[("test.status/v1", "status")] = "ready"

    @override
    def describe(self) -> InstrumentDescription:
        description = super().describe()
        return description.model_copy(
            update={
                "interfaces": [
                    *description.interfaces,
                    interface(
                        "test.status/v1",
                        properties=[string_property("status", access="read_only")],
                    ),
                ]
            }
        )


class _Provider:
    provider_id = "tests.run_provider"

    def __init__(
        self,
        *,
        fail_action: _FailAction = None,
        apply_barrier: Barrier | None = None,
        driver_type: type[_Driver] = _Driver,
    ) -> None:
        self.fail_action: _FailAction = fail_action
        self.apply_barrier = apply_barrier
        self.driver_type = driver_type
        self.connect_count = 0
        self.drivers: list[_Driver] = []

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=tuple(
                self.driver_type(item.id).describe() for item in context.bindings
            ),
        )

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> _Driver:
        self.connect_count += 1
        driver = self.driver_type(
            context.binding.id,
            fail_action=self.fail_action,
            apply_barrier=self.apply_barrier,
        )
        self.drivers.append(driver)
        return driver


class _SecondRejectingProvider(_Provider):
    @override
    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> _Driver:
        self.connect_count += 1
        driver = _Driver(
            context.binding.id,
            fail_action=("reject_apply" if context.binding.id == "source-1" else None),
        )
        self.drivers.append(driver)
        return driver


def test_batch_reconciles_state_collects_values_and_replays_once(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_StateEvidenceDriver)
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            load_config(),
            driver_type=_StateEvidenceDriver,
        )
        instruments = runtime.application.instruments
        provision = instruments.provision_run(run_id, _provision(lease_id))
        assert provision.observed_state[0].instrument_id == "source-0"
        assert provision.baseline_state[0].instrument_id == "source-0"
        assert provision.baseline_state == provision.observed_state
        [driver] = provider.drivers

        command = _batch_command(
            lease_id,
            "batch-1",
            _apply_action("source-0", effect_id="apply-1"),
            _collect_action("source-0", effect_id="collect-1"),
        )
        before_collect = datetime.now(UTC)
        receipt = instruments.execute_run_hardware(run_id, command)
        after_collect = datetime.now(UTC)
        assert instruments.execute_run_hardware(run_id, command) == receipt
        assert [(value.value_id, value.value) for value in receipt.values] == [
            (
                "signal-use",
                MeasurementScalar.create(dtype="float64", value=1.0, unit="ratio"),
            )
        ]
        [collected] = receipt.values
        assert collected.evidence.command_id == "collect-1"
        assert collected.evidence.instrument_id == "source-0"
        assert collected.evidence.interface_id == "test.scalar_signal/v1"
        assert collected.evidence.component_path == ()
        assert collected.evidence.acquisition_id == "sample"
        assert collected.evidence.result_id == "signal"
        assert (
            before_collect
            <= collected.evidence.started_at
            <= collected.evidence.completed_at
            <= after_collect
        )
        assert len(driver.applied) == 1
        assert driver.read_count == 2
        assert len(driver.collect_requests) == 1
        [state_action] = receipt.state_actions
        assert state_action.operation_id == "apply-1"
        assert state_action.instrument_id == "source-0"
        assert state_action.point_index == 0
        assert state_action.status == "applied"
        [assignment] = state_action.assignments
        applied_action = command.batch.actions[0]
        assert isinstance(applied_action, RunHardwareApply)
        assert assignment.target == applied_action.assignments[0].target
        assert assignment.value == StateValue(Quantity(value=5.0, unit="GHz"))
        assert state_action.metadata == {
            "hold_mode": "end_with_keep",
            "analog_readback": False,
        }

        unchanged = _batch_command(
            lease_id,
            "batch-2",
            _apply_action("source-0", effect_id="apply-2"),
            sequence=1,
        )
        unchanged_receipt = instruments.execute_run_hardware(run_id, unchanged)
        assert not unchanged_receipt.problems
        [unchanged_action] = unchanged_receipt.state_actions
        assert unchanged_action.operation_id == "apply-2"
        assert unchanged_action.status == "unchanged"
        assert unchanged_action.assignments == ()
        assert unchanged_action.metadata == {}
        assert len(driver.applied) == 1
        batch_events = [
            event
            for event in runtime.application.runs.list_events(
                limit=100,
                after=None,
                run_id=run_id,
            ).items
            if event.kind.startswith("run_hardware_batch_")
        ]
        assert batch_events == []


def test_batch_prepares_selected_acquisitions_before_trigger_invoke(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_PreparingDriver)
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            load_config(),
            driver_type=_PreparingDriver,
        )
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        payload = command_payload_from_bytes(
            id="program-1",
            schema_id="pulse_program",
            codec_id="tests.canonical-json",
            codec_version=1,
            media_type="application/json",
            content=b'{"samples":[0.0]}',
        )

        receipt = instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "prepared-capture",
                _invoke_action(
                    "source-0",
                    effect_id="trigger",
                    payload=payload,
                ),
                _collect_action("source-0", effect_id="fetch"),
            ),
        )

        assert receipt.problems == ()
        [driver] = provider.drivers
        assert isinstance(driver, _PreparingDriver)
        assert driver.events == ["prepare", "invoke", "collect"]
        [plan] = driver.acquisition_plans
        [planned] = plan.acquisitions
        assert planned.target == InterfaceRef("test.scalar_signal/v1").acquisition(
            "sample"
        )
        assert {result.result_id for result in planned.results} == {"signal"}


def test_failed_batch_returns_state_actions_confirmed_before_rejection(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_RejectingCollectEvidenceDriver)
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            load_config(),
            driver_type=_RejectingCollectEvidenceDriver,
        )
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        command = _batch_command(
            lease_id,
            "apply-before-rejected-collect",
            _apply_action("source-0", effect_id="apply-confirmed"),
            _collect_action("source-0", effect_id="collect-rejected"),
        )

        receipt = instruments.execute_run_hardware(run_id, command)
        replay = instruments.execute_run_hardware(run_id, command)

        assert replay == receipt
        assert [item.code for item in receipt.problems] == [
            "test_collect_rejected_after_apply"
        ]
        [state_action] = receipt.state_actions
        assert state_action.operation_id == "apply-confirmed"
        assert state_action.status == "applied"
        assert state_action.metadata["hold_mode"] == "end_with_keep"
        [driver] = provider.drivers
        assert len(driver.applied) == 1
        assert len(driver.collect_requests) == 1


def test_run_start_applies_default_state_after_fresh_observation(
    tmp_path: Path,
) -> None:
    provider = _Provider()
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.1, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, config)
        instruments = runtime.application.instruments

        provision = instruments.provision_run(run_id, _provision(lease_id))
        replay = instruments.provision_run(run_id, _provision(lease_id))

        assert replay == provision
        [observed] = provision.observed_state
        [reconciled] = provision.baseline_state
        assert {
            (item.target.interface_id, item.target.property_id): item.value.root
            for item in observed.observations
            if item.target.kind == "interface"
        } == {
            ("test.set_frequency/v1", "frequency"): Quantity(
                value=4.0,
                unit="GHz",
            ),
            ("test.set_gain/v1", "gain"): 0.0,
        }
        assert {
            (item.target.interface_id, item.target.property_id): item.value.root
            for item in reconciled.observations
            if item.target.kind == "interface"
        } == {
            ("test.set_frequency/v1", "frequency"): Quantity(
                value=5.1,
                unit="GHz",
            ),
            ("test.set_gain/v1", "gain"): 0.0,
        }
        [driver] = provider.drivers
        assert len(driver.applied) == 1
        [target] = driver.applied[0].values
        assert target.property_id == "frequency"


def test_run_start_preserves_observed_state_when_default_state_exists(
    tmp_path: Path,
) -> None:
    provider = _Provider()
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.0, unit="GHz")),
        ),
        run_start="preserve",
    )
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, config)

        provision = runtime.application.instruments.provision_run(
            run_id,
            _provision(lease_id),
        )

        assert provision.baseline_state == provision.observed_state
        [driver] = provider.drivers
        assert driver.applied == []


def test_unknown_default_state_reconciliation_quarantines_the_run(
    tmp_path: Path,
) -> None:
    provider = _Provider(fail_action="apply")
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.0, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, config)

        with pytest.raises(
            BackendConflict,
            match=r"default-state reconciliation .* failed with unknown",
        ):
            runtime.application.instruments.provision_run(
                run_id,
                _provision(lease_id),
            )

        [driver] = provider.drivers
        assert driver.abort_count == 1
        assert driver.disconnect_count == 1
        durable = runtime.application.executor._control.get_run(run_id)
        assert durable.state == "attention_required"
        assert (
            durable.attention_reason == "run_instrument_default_reconciliation_unknown"
        )
        _assert_run_state_discarded(runtime.application.instruments, run_id)


def test_unknown_run_start_attempts_safe_operation_before_quarantine(
    tmp_path: Path,
) -> None:
    provider = _Provider(
        fail_action="apply",
        driver_type=_SafeOperationDriver,
    )
    config = _config_with_safe_operation(
        _config_with_default_state(
            _setting(
                interface_id="test.set_frequency/v1",
                property_id="frequency",
                value=StateValue(Quantity(value=5.0, unit="GHz")),
            )
        ),
        failure_action="abort_then_safe_state",
        required=True,
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            config,
            driver_type=_SafeOperationDriver,
        )

        with pytest.raises(
            BackendConflict,
            match=r"default-state reconciliation .* failed with unknown",
        ):
            runtime.application.instruments.provision_run(
                run_id,
                _provision(lease_id),
            )

        [driver] = provider.drivers
        assert driver.abort_count == 1
        [invocation] = driver.invoked
        assert invocation.target.operation_id == "enter"
        assert driver.disconnect_count == 1


def test_run_start_requires_default_state_to_converge(tmp_path: Path) -> None:
    provider = _Provider(driver_type=_NonConvergingDriver)
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.0, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            config,
            driver_type=_NonConvergingDriver,
        )

        with pytest.raises(
            BackendConflict,
            match=r"default-state reconciliation .* failed with unknown",
        ):
            runtime.application.instruments.provision_run(
                run_id,
                _provision(lease_id),
            )

        [driver] = provider.drivers
        assert len(driver.applied) == 1
        assert driver.abort_count == 1
        assert driver.disconnect_count == 1
        durable = runtime.application.executor._control.get_run(run_id)
        assert durable.state == "attention_required"
        assert (
            durable.attention_reason == "run_instrument_default_reconciliation_unknown"
        )


def test_rejected_default_state_reconciliation_releases_without_quarantine(
    tmp_path: Path,
) -> None:
    provider = _Provider(fail_action="reject_apply")
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.0, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, config)
        instruments = runtime.application.instruments

        receipt = instruments.provision_run(run_id, _provision(lease_id))

        assert receipt.status == "rejected"
        assert [item.code for item in receipt.problems] == ["test_apply_rejected"]
        [driver] = provider.drivers
        assert driver.abort_count == 0
        assert driver.disconnect_count == 0
        assert runtime.application.executor._control.get_run(run_id).state != (
            "attention_required"
        )
        assert instruments._run_contexts[run_id].runtime is None


def test_partial_default_state_reconciliation_releases_confirmed_state(
    tmp_path: Path,
) -> None:
    provider = _SecondRejectingProvider()
    config = _two_instrument_config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=5.0, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            config,
            host_instrument_order=("source-0", "source-1"),
        )

        receipt = runtime.application.instruments.provision_run(
            run_id,
            _provision(lease_id),
        )

        assert receipt.status == "rejected"
        assert [item.code for item in receipt.problems] == ["test_apply_rejected"]
        first, second = provider.drivers
        assert len(first.applied) == 1
        assert second.applied == []
        assert [driver.abort_count for driver in provider.drivers] == [0, 0]
        assert [driver.disconnect_count for driver in provider.drivers] == [0, 0]
        durable = runtime.application.executor._control.get_run(run_id)
        assert durable.state != "attention_required"
        assert runtime.application.instruments._run_contexts[run_id].runtime is None
        runtime.application.executor.commit_terminal(
            run_id,
            TerminalRunCommitCommand(
                lease_id=lease_id,
                outcome=RunOutcome(
                    run_id=run_id,
                    result="failed",
                    certainty="known",
                    problems=receipt.problems,
                ),
            ),
        )

        next_run_id, next_lease_id = _start_run(
            runtime,
            config,
            host_instrument_order=("source-0",),
            submission_id="reacquire-after-rejection",
        )
        reacquired = runtime.application.instruments.provision_run(
            next_run_id,
            _provision(next_lease_id),
        )
        assert reacquired.status == "ready"
        [observed] = reacquired.observed_state
        assert {
            (item.target.interface_id, item.target.property_id): item.value.root
            for item in observed.observations
            if item.target.kind == "interface"
        }[("test.set_frequency/v1", "frequency")] == Quantity(
            value=5.0,
            unit="GHz",
        )
        assert provider.connect_count == 2
        assert first.read_count == 3
        assert len(first.applied) == 1


def test_run_start_skips_default_state_matching_observed_state(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_VariantDriver)
    config = _config_with_default_state(
        _setting(
            interface_id="test.dc/v1",
            property_id="mode",
            value=StateValue("voltage"),
        ),
        _setting(
            interface_id="test.dc/v1",
            property_id="voltage_level",
            value=StateValue(0.1),
        ),
        _setting(
            interface_id="test.dc/v1",
            property_id="output_enabled",
            value=StateValue(False),
        ),
    )
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            config,
            driver_type=_VariantDriver,
        )

        receipt = runtime.application.instruments.provision_run(
            run_id,
            _provision(lease_id),
        )

        assert receipt.status == "ready"
        assert receipt.baseline_state == receipt.observed_state
        [driver] = provider.drivers
        assert driver.applied == []


def test_run_start_skips_unit_equivalent_default_state(tmp_path: Path) -> None:
    provider = _Provider(driver_type=_EquivalentQuantityDriver)
    config = _config_with_default_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=1000.0, unit="MHz")),
        )
    )
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            config,
            driver_type=_EquivalentQuantityDriver,
        )

        receipt = runtime.application.instruments.provision_run(
            run_id,
            _provision(lease_id),
        )

        assert receipt.baseline_state == receipt.observed_state
        [driver] = provider.drivers
        assert driver.applied == []


def test_batch_retry_expires_after_later_progress(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_VariantDriver)
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            load_config(),
            driver_type=_VariantDriver,
        )
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        [driver] = provider.drivers
        voltage = _batch_command(
            lease_id,
            "voltage-batch",
            _variant_apply_action(
                effect_id="voltage-level",
                voltage_level=0.2,
            ),
        )

        first = instruments.execute_run_hardware(run_id, voltage)
        switched = instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "current-batch",
                _variant_apply_action(
                    effect_id="switch-current",
                    mode="current",
                    current_level=0.02,
                ),
                sequence=1,
            ),
        )

        assert not first.problems
        assert not switched.problems
        with pytest.raises(BackendConflict, match="receipt has expired"):
            instruments.execute_run_hardware(run_id, voltage)
        assert len(driver.applied) == 2


def test_batch_preflights_state_sized_axes_from_opening_and_projected_state(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_StateSizedAxisDriver)
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            load_config(),
            driver_type=_StateSizedAxisDriver,
        )
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        [driver] = provider.drivers

        opening_mismatch = instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "opening-axis-mismatch",
                _state_sized_axis_collect_action(
                    effect_id="collect-opening-mismatch",
                    size=2,
                ),
            ),
        )

        assert [issue.code for issue in opening_mismatch.problems] == [
            "instrument_driver_acquisition_axis_state_mismatch"
        ]
        assert driver.applied == []
        assert driver.collect_requests == []

        projected_match = instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "project-axis-match",
                _state_sized_axis_apply_action(
                    effect_id="set-four-points",
                    points=4,
                ),
                _state_sized_axis_collect_action(
                    effect_id="collect-four-points",
                    size=4,
                ),
                sequence=1,
            ),
        )

        assert projected_match.problems == ()
        assert len(driver.applied) == 1
        assert len(driver.collect_requests) == 1

        side_effect_counts = (
            driver.read_count,
            len(driver.applied),
            len(driver.collect_requests),
        )
        projected_mismatch = instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "project-axis-mismatch",
                _state_sized_axis_apply_action(
                    effect_id="set-five-points",
                    points=5,
                ),
                _state_sized_axis_collect_action(
                    effect_id="collect-four-after-five",
                    size=4,
                ),
                sequence=2,
            ),
        )

        assert [issue.code for issue in projected_mismatch.problems] == [
            "instrument_driver_acquisition_axis_state_mismatch"
        ]
        assert (
            driver.read_count,
            len(driver.applied),
            len(driver.collect_requests),
        ) == side_effect_counts


def test_batch_projects_acquisition_preconditions_before_any_side_effect(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_PreconditionVariantDriver)
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            load_config(),
            driver_type=_PreconditionVariantDriver,
        )
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        [driver] = provider.drivers

        rejected_command = _batch_command(
            lease_id,
            "collect-with-disabled-output",
            _variant_apply_action(
                effect_id="change-level-before-rejected-collect",
                voltage_level=0.2,
            ),
            _variant_collect_action(
                effect_id="collect-disabled-output",
                result_id="monitored_voltage",
            ),
        )
        rejected = instruments.execute_run_hardware(
            run_id,
            rejected_command,
        )

        assert [issue.code for issue in rejected.problems] == [
            "instrument_driver_acquisition_precondition_not_met"
        ]
        [issue] = rejected.problems
        assert isinstance(issue.location, RuntimeLocation)
        assert issue.location.operation_id == "collect-disabled-output"
        assert issue.location.instrument_id == "source-0"
        assert isinstance(issue.related_locations[0], ModelLocation)
        assert issue.related_locations[0].root == "instrument_collect_command"
        assert isinstance(issue.related_locations[1], ModelLocation)
        assert issue.related_locations[1].root == "instrument_state"
        assert driver.applied == []
        assert driver.collect_requests == []

        accepted = instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "enable-output-before-collect",
                _variant_apply_action(
                    effect_id="enable-output",
                    output_enabled=True,
                ),
                _variant_collect_action(
                    effect_id="collect-enabled-output",
                    result_id="monitored_voltage",
                ),
                sequence=1,
            ),
        )

        assert accepted.problems == ()
        assert len(driver.applied) == 1
        assert len(driver.collect_requests) == 1

        with pytest.raises(BackendConflict, match="receipt has expired"):
            instruments.execute_run_hardware(run_id, rejected_command)
        assert len(driver.applied) == 1
        assert len(driver.collect_requests) == 1


def test_live_collect_rejection_preserves_problem_context_and_replays(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_VariantDriver)
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            load_config(),
            driver_type=_VariantDriver,
        )
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        [driver] = provider.drivers
        assert isinstance(driver, _VariantDriver)
        driver.mode = "current"
        command = _batch_command(
            lease_id,
            "collect-after-external-mode-change",
            _variant_collect_action(
                effect_id="collect-stale-voltage",
                result_id="monitored_voltage",
            ),
        )

        receipt = instruments.execute_run_hardware(run_id, command)
        replay = instruments.execute_run_hardware(run_id, command)

        assert replay == receipt
        [issue] = receipt.problems
        assert issue.code == "test_acquisition_result_inactive"
        assert isinstance(issue.location, RuntimeLocation)
        assert issue.location.run_id == run_id
        assert issue.location.operation_id == "collect-stale-voltage"
        assert isinstance(issue.related_locations[0], ModelLocation)
        assert issue.related_locations[0].root == "instrument_state"
        assert len(driver.collect_requests) == 1

        recovered = instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "collect-after-rejection-resync",
                _variant_collect_action(
                    effect_id="collect-current-after-resync",
                    result_id="monitored_current",
                ),
                sequence=1,
            ),
        )

        assert recovered.problems == ()
        assert len(driver.collect_requests) == 2


def test_run_invoke_without_invalidations_reuses_cached_state(
    tmp_path: Path,
) -> None:
    provider = _Provider()
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, load_config())
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        [driver] = provider.drivers
        reads_before_invoke = driver.read_count
        payload = command_payload_from_bytes(
            id="program-1",
            schema_id="pulse_program",
            codec_id="tests.canonical-json",
            codec_version=1,
            media_type="application/json",
            content=b'{"samples":[0.0]}',
        )

        receipt = instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "invoke-batch",
                _invoke_action(
                    "source-0",
                    effect_id="invoke-1",
                    payload=payload,
                ),
            ),
        )

        assert receipt.problems == ()
        assert len(driver.invoked) == 1
        assert driver.read_count == reads_before_invoke
        [argument] = driver.invoked[0].arguments.values()
        assert isinstance(argument, DriverPayload)
        assert argument.schema_id == payload.schema_id
        assert argument.value == {"samples": [0.0]}


def test_provision_rejects_contract_changed_after_admission(tmp_path: Path) -> None:
    provider = _Provider()
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            load_config(),
            contract_fingerprint="0" * 64,
        )

        receipt = runtime.application.instruments.provision_run(
            run_id,
            _provision(lease_id),
        )

        assert receipt.status == "rejected"
        assert [item.code for item in receipt.problems] == [
            "instrument_contract_changed_after_admission"
        ]
        assert provider.connect_count == 0


def test_batch_id_rejects_different_content(tmp_path: Path) -> None:
    provider = _Provider()
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, load_config())
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        first = _batch_command(
            lease_id,
            "batch-1",
            _apply_action("source-0", effect_id="apply-1"),
        )
        instruments.execute_run_hardware(run_id, first)

        changed = _batch_command(
            lease_id,
            "batch-1",
            _collect_action("source-0", effect_id="collect-1"),
        )
        with pytest.raises(BackendConflict, match="different operation content"):
            instruments.execute_run_hardware(run_id, changed)


def test_batch_sequence_rejects_gap_before_hardware_effect(tmp_path: Path) -> None:
    provider = _Provider()
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, load_config())
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        [driver] = provider.drivers
        command = _batch_command(
            lease_id,
            "batch-2",
            _apply_action("source-0", effect_id="apply-2"),
            sequence=1,
        )

        with pytest.raises(BackendConflict, match="sequence has a gap"):
            instruments.execute_run_hardware(run_id, command)

        assert driver.applied == []


def test_unknown_driver_action_quarantines_and_discards_run_state(
    tmp_path: Path,
) -> None:
    provider = _Provider(fail_action="apply")
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, load_config())
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))

        receipt = instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "batch-1",
                _apply_action("source-0", effect_id="apply-1"),
            ),
        )
        assert receipt.indeterminate
        [issue] = receipt.problems
        assert issue.code == "instrument_apply_unknown"
        [saved] = [
            event
            for event in runtime.application.runs.list_events(
                limit=100, after=None, run_id=run_id
            ).items
            if event.kind == "run_hardware_batch_unknown"
        ]
        assert saved.payload["problems"] == [issue.model_dump(mode="json")]

        [driver] = provider.drivers
        assert driver.apply_call_count == 1
        events_before = runtime.application.runs.list_events(
            limit=100, after=None, run_id=run_id
        ).items
        with pytest.raises(BackendConflict, match=r"not provisioned|lease"):
            instruments.execute_run_hardware(
                run_id,
                _batch_command(
                    lease_id,
                    "stale-after-unknown",
                    _apply_action("source-0", effect_id="stale-apply"),
                ),
            )
        assert driver.apply_call_count == 1
        assert (
            runtime.application.runs.list_events(
                limit=100, after=None, run_id=run_id
            ).items
            == events_before
        )
        assert driver.abort_count == 1
        assert driver.disconnect_count == 1
        assert runtime.application.executor._control.get_run(run_id).state == (
            "attention_required"
        )
        _assert_run_state_discarded(instruments, run_id)


@pytest.mark.parametrize("quarantine_fails", [False, True])
def test_unknown_action_audit_failure_still_discards_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quarantine_fails: bool
) -> None:
    provider = _Provider(fail_action="apply")
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, load_config())
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        audit_error = OSError("audit disk unavailable")

        def fail_audit(*args: object, **kwargs: object) -> None:
            raise audit_error

        def fail_quarantine(*args: object, **kwargs: object) -> None:
            raise OSError("quarantine disk unavailable")

        monkeypatch.setattr(instruments, "_record_run_operation_event", fail_audit)
        if quarantine_fails:
            monkeypatch.setattr(
                instruments._control, "mark_executor_unknown", fail_quarantine
            )
        command = _batch_command(
            lease_id, "audit-failure", _apply_action("source-0", effect_id="apply-1")
        )
        with pytest.raises(OSError, match="audit disk unavailable") as caught:
            instruments.execute_run_hardware(run_id, command)
        assert caught.value is audit_error
        if quarantine_fails:
            assert isinstance(caught.value.__cause__, OSError)
            assert str(caught.value.__cause__) == "quarantine disk unavailable"
        _assert_run_state_discarded(instruments, run_id)
        [driver] = provider.drivers
        assert driver.apply_call_count == 1
        assert driver.abort_count == driver.disconnect_count == 1
        with pytest.raises(BackendConflict, match=r"not provisioned|lease"):
            instruments.execute_run_hardware(run_id, command)
        assert driver.apply_call_count == 1
        assert not any(
            event.kind == "run_hardware_batch_unknown"
            for event in runtime.application.runs.list_events(
                limit=100, after=None, run_id=run_id
            ).items
        )


def test_unknown_collect_receipt_preserves_driver_diagnostics(
    tmp_path: Path,
) -> None:
    provider = _Provider(fail_action="unknown_collect_receipt")
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, load_config())
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))

        receipt = instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "unknown-collect-receipt",
                _collect_action("source-0", effect_id="collect-unknown"),
            ),
        )

        assert receipt.indeterminate
        [issue] = receipt.problems
        assert issue.code == "test_collect_receipt_unknown"
        assert isinstance(issue.location, RuntimeLocation)
        assert issue.location.operation_id == "collect-unknown"
        assert isinstance(issue.related_locations[0], ModelLocation)
        assert issue.related_locations[0].root == "driver_acquisition"
        [driver] = provider.drivers
        assert driver.abort_count == 1
        assert driver.disconnect_count == 1
        assert runtime.application.executor._control.get_run(run_id).state == (
            "attention_required"
        )
        [unknown] = [
            event
            for event in runtime.application.runs.list_events(
                limit=100,
                after=None,
                run_id=run_id,
            ).items
            if event.kind == "run_hardware_batch_unknown"
        ]
        assert unknown.payload["status"] == "unknown"
        assert unknown.payload["sequence"] == 0
        assert unknown.payload["completed_effect_ids"] == []
        assert unknown.payload["problem_codes"] == ["test_collect_receipt_unknown"]
        _assert_run_state_discarded(instruments, run_id)


def test_unknown_invoke_quarantines_and_discards_run_state(
    tmp_path: Path,
) -> None:
    provider = _Provider(fail_action="invoke")
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, load_config())
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        payload = command_payload_from_bytes(
            id="program-1",
            schema_id="pulse_program",
            codec_id="tests.canonical-json",
            codec_version=1,
            media_type="application/json",
            content=b'{"samples":[0.0]}',
        )

        receipt = instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "invoke-unknown",
                _invoke_action(
                    "source-0",
                    effect_id="invoke-unknown-1",
                    payload=payload,
                ),
            ),
        )
        assert receipt.indeterminate
        [issue] = receipt.problems
        assert issue.code == "instrument_invoke_unknown"
        [saved] = [
            event
            for event in runtime.application.runs.list_events(
                limit=100, after=None, run_id=run_id
            ).items
            if event.kind == "run_hardware_batch_unknown"
        ]
        assert saved.payload["problems"] == [issue.model_dump(mode="json")]

        [driver] = provider.drivers
        assert len(driver.invoked) == 1
        events_before = runtime.application.runs.list_events(
            limit=100, after=None, run_id=run_id
        ).items
        with pytest.raises(BackendConflict, match=r"not provisioned|lease"):
            instruments.execute_run_hardware(
                run_id,
                _batch_command(
                    lease_id,
                    "stale-after-unknown",
                    _invoke_action(
                        "source-0", effect_id="stale-invoke", payload=payload
                    ),
                ),
            )
        assert len(driver.invoked) == 1
        assert (
            runtime.application.runs.list_events(
                limit=100, after=None, run_id=run_id
            ).items
            == events_before
        )
        assert len(driver.invoked) == 1
        assert driver.abort_count == 1
        assert driver.disconnect_count == 1
        assert runtime.application.executor._control.get_run(run_id).state == (
            "attention_required"
        )
        _assert_run_state_discarded(instruments, run_id)


def test_finish_reads_terminal_state_releases_and_replays_concurrently(
    tmp_path: Path,
) -> None:
    provider = _Provider()
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, load_config())
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        [driver] = provider.drivers
        command = RunHardwareFinishCommand(
            lease_id=lease_id,
            operation_id="hardware.finish",
            failed=False,
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = tuple(
                future.result(timeout=3)
                for future in (
                    pool.submit(instruments.finish_run_hardware, run_id, command),
                    pool.submit(instruments.finish_run_hardware, run_id, command),
                )
            )
        receipt, replay = receipts
        assert replay == receipt
        with pytest.raises(
            BackendConflict,
            match="finalized with different content",
        ):
            instruments.finish_run_hardware(
                run_id,
                command.model_copy(update={"failed": True}),
            )
        assert receipt.final_state[0].instrument_id == "source-0"
        assert driver.abort_count == 0
        assert driver.disconnect_count == 0
        assert driver.read_count == 2
        context = instruments._run_contexts[run_id]
        assert context.provision is None
        assert context.runtime is None
        assert context.finalization is not None
        instruments.release_run(run_id)
        _assert_run_state_discarded(instruments, run_id)


def test_unprovisioned_run_operations_do_not_leave_contexts(tmp_path: Path) -> None:
    provider = _Provider()
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, load_config())
        instruments = runtime.application.instruments

        with pytest.raises(BackendConflict, match="not provisioned"):
            instruments.authorize_run_payload_upload(run_id, lease_id)
        with pytest.raises(BackendConflict, match="not provisioned"):
            instruments.execute_run_hardware(
                run_id,
                _batch_command(
                    lease_id,
                    "unprovisioned-batch",
                    _apply_action("source-0", effect_id="unprovisioned-apply"),
                ),
            )
        with pytest.raises(BackendConflict, match="not provisioned"):
            instruments.finish_run_hardware(
                run_id,
                RunHardwareFinishCommand(
                    lease_id=lease_id,
                    operation_id="unprovisioned-finish",
                    failed=False,
                ),
            )
        instruments.finalize_run(run_id, token=lease_id)

        assert run_id not in instruments._run_contexts


def test_failed_finish_abort_failure_is_unknown(tmp_path: Path) -> None:
    provider = _Provider(fail_action="abort")
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, load_config())
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))

        with pytest.raises(BackendConflict, match="abort failed with unknown state"):
            instruments.finish_run_hardware(
                run_id,
                RunHardwareFinishCommand(
                    lease_id=lease_id,
                    operation_id="hardware.finish",
                    failed=True,
                ),
            )

        [driver] = provider.drivers
        assert driver.abort_count == 1
        assert driver.disconnect_count == 1
        _assert_run_state_discarded(instruments, run_id)


def test_failed_finish_applies_configured_safe_state(tmp_path: Path) -> None:
    provider = _Provider()
    safe_frequency = _setting(
        interface_id="test.set_frequency/v1",
        property_id="frequency",
        value=StateValue(Quantity(value=4.25, unit="GHz")),
    )
    config = _config_with_safe_state(safe_frequency)
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(runtime, config)
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))

        receipt = instruments.finish_run_hardware(
            run_id,
            RunHardwareFinishCommand(
                lease_id=lease_id,
                operation_id="hardware.finish",
                failed=True,
            ),
        )

        [driver] = provider.drivers
        assert driver.abort_count == 1
        [applied] = driver.applied
        [target] = applied.values
        assert target.property_id == "frequency"
        [final_state] = receipt.final_state
        values = {
            (item.target.interface_id, item.target.property_id): item.value.root
            for item in final_state.observations
            if item.target.kind == "interface"
        }
        assert values[("test.set_frequency/v1", "frequency")] == Quantity(
            value=4.25,
            unit="GHz",
        )
        assert [(action.kind, action.status) for action in receipt.actions] == [
            ("abort", "completed"),
            ("safe_state", "completed"),
        ]
        assert receipt.problems == ()


def test_successful_finish_does_not_apply_failure_safe_state(tmp_path: Path) -> None:
    provider = _Provider()
    config = _config_with_safe_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=4.25, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(runtime, config)
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))

        instruments.finish_run_hardware(
            run_id,
            RunHardwareFinishCommand(
                lease_id=lease_id,
                operation_id="hardware.finish",
                failed=False,
            ),
        )

        [driver] = provider.drivers
        assert driver.abort_count == 0
        assert driver.applied == []


def test_successful_finish_runs_configured_safe_operation_and_records_evidence(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_SafeOperationDriver)
    config = _config_with_safe_operation(
        success_action="apply_safe_state",
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            config,
            driver_type=_SafeOperationDriver,
        )
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))

        receipt = instruments.finish_run_hardware(
            run_id,
            RunHardwareFinishCommand(
                lease_id=lease_id,
                operation_id="hardware.finish",
                failed=False,
            ),
        )

        [driver] = provider.drivers
        [invocation] = driver.invoked
        assert invocation.target.interface_id == "test.safe_state/v1"
        assert invocation.target.operation_id == "enter"
        [action] = receipt.actions
        assert action.kind == "safe_operation"
        assert action.status == "completed"
        assert action.metadata == {"device_status": "safe"}


def test_failed_finish_records_abort_safe_operation_and_patch_in_order(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_SafeOperationDriver)
    config = _config_with_safe_operation(
        _config_with_safe_state(
            _setting(
                interface_id="test.set_frequency/v1",
                property_id="frequency",
                value=StateValue(Quantity(value=4.25, unit="GHz")),
            )
        ),
        failure_action="abort_then_safe_state",
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            config,
            driver_type=_SafeOperationDriver,
        )
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))

        receipt = instruments.finish_run_hardware(
            run_id,
            RunHardwareFinishCommand(
                lease_id=lease_id,
                operation_id="hardware.finish",
                failed=True,
            ),
        )

        assert [(action.kind, action.status) for action in receipt.actions] == [
            ("abort", "completed"),
            ("safe_operation", "completed"),
            ("safe_state", "completed"),
        ]


def test_required_safe_operation_rejection_quarantines_the_run(
    tmp_path: Path,
) -> None:
    provider = _Provider(
        fail_action="reject_invoke",
        driver_type=_SafeOperationDriver,
    )
    config = _config_with_safe_operation(
        success_action="apply_safe_state",
        required=True,
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            config,
            driver_type=_SafeOperationDriver,
        )
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))

        with pytest.raises(BackendConflict, match="safe operation was rejected"):
            instruments.finish_run_hardware(
                run_id,
                RunHardwareFinishCommand(
                    lease_id=lease_id,
                    operation_id="hardware.finish",
                    failed=False,
                ),
            )

        [driver] = provider.drivers
        assert driver.abort_count == 1
        assert driver.disconnect_count == 1
        durable = runtime.application.executor._control.get_run(run_id)
        assert durable.state == "attention_required"
        assert durable.attention_reason == (
            "run_instrument_required_safe_state_rejected"
        )
        _assert_run_state_discarded(instruments, run_id)


def test_successful_finish_restores_the_preserved_baseline(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_ReadOnlyStateDriver)
    config = _config_with_success_action(
        load_config(),
        success_action="restore_baseline",
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            config,
            driver_type=_ReadOnlyStateDriver,
        )
        instruments = runtime.application.instruments
        provision = instruments.provision_run(run_id, _provision(lease_id))
        instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "change-frequency",
                _apply_action("source-0", effect_id="change-frequency"),
            ),
        )

        receipt = instruments.finish_run_hardware(
            run_id,
            RunHardwareFinishCommand(
                lease_id=lease_id,
                operation_id="hardware.finish",
                failed=False,
            ),
        )

        [baseline] = provision.baseline_state
        [final] = receipt.final_state
        assert [(item.target, item.value) for item in final.observations] == [
            (item.target, item.value) for item in baseline.observations
        ]
        [driver] = provider.drivers
        assert driver.abort_count == 0
        assert len(driver.applied) == 2
        restored = driver.applied[-1]
        assert {
            (target.interface_id, target.property_id): value
            for target, value in restored.values.items()
            if isinstance(target, PropertyRef)
        } == {
            ("test.set_frequency/v1", "frequency"): Quantity(
                value=4.0,
                unit="GHz",
            )
        }
        [action] = receipt.actions
        assert action.kind == "restore_baseline"
        assert action.status == "completed"


def test_successful_finish_records_but_does_not_restore_unmarked_state(
    tmp_path: Path,
) -> None:
    provider = _Provider()
    config = _config_with_success_action(
        load_config(),
        success_action="restore_baseline",
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(runtime, config)
        instruments = runtime.application.instruments
        provision = instruments.provision_run(run_id, _provision(lease_id))
        [driver] = provider.drivers
        driver._state[("test.set_gain/v1", "gain")] = 7.0

        receipt = instruments.finish_run_hardware(
            run_id,
            RunHardwareFinishCommand(
                lease_id=lease_id,
                operation_id="hardware.finish",
                failed=False,
            ),
        )

        [baseline] = provision.baseline_state
        [final] = receipt.final_state
        baseline_gain = next(
            observation.value.root
            for observation in baseline.observations
            if observation.target.property_id == "gain"
        )
        final_gain = next(
            observation.value.root
            for observation in final.observations
            if observation.target.property_id == "gain"
        )
        assert baseline_gain == 0.0
        assert final_gain == 7.0
        assert driver.applied == []


def test_successful_finish_restores_the_default_baseline(tmp_path: Path) -> None:
    provider = _Provider()
    config = _config_with_success_action(
        _config_with_default_state(
            _setting(
                interface_id="test.set_frequency/v1",
                property_id="frequency",
                value=StateValue(Quantity(value=5.1, unit="GHz")),
            )
        ),
        success_action="restore_baseline",
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(runtime, config)
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        instruments.execute_run_hardware(
            run_id,
            _batch_command(
                lease_id,
                "change-frequency",
                _apply_action("source-0", effect_id="change-frequency"),
            ),
        )

        receipt = instruments.finish_run_hardware(
            run_id,
            RunHardwareFinishCommand(
                lease_id=lease_id,
                operation_id="hardware.finish",
                failed=False,
            ),
        )

        [final] = receipt.final_state
        frequency = next(
            item.value.root
            for item in final.observations
            if item.target.property_id == "frequency"
        )
        assert frequency == Quantity(value=5.1, unit="GHz")
        [driver] = provider.drivers
        assert len(driver.applied) == 3


def test_rejected_baseline_restore_fails_and_aborts(tmp_path: Path) -> None:
    provider = _Provider()
    config = _config_with_success_action(
        load_config(),
        success_action="restore_baseline",
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(runtime, config)
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        [driver] = provider.drivers
        driver._state[("test.set_frequency/v1", "frequency")] = Quantity(
            value=5.0,
            unit="GHz",
        )
        driver.fail_action = "reject_apply"

        receipt = instruments.finish_run_hardware(
            run_id,
            RunHardwareFinishCommand(
                lease_id=lease_id,
                operation_id="hardware.finish",
                failed=False,
            ),
        )

        assert [item.code for item in receipt.problems] == ["test_apply_rejected"]
        assert driver.abort_count == 1
        assert driver.disconnect_count == 0
        assert runtime.application.executor._control.get_run(run_id).state != (
            "attention_required"
        )


def test_rejected_baseline_restore_enters_configured_safe_state(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_RejectNextApplyDriver)
    config = _config_with_success_action(
        _config_with_safe_state(
            _setting(
                interface_id="test.set_frequency/v1",
                property_id="frequency",
                value=StateValue(Quantity(value=4.25, unit="GHz")),
            )
        ),
        success_action="restore_baseline",
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            config,
            driver_type=_RejectNextApplyDriver,
        )
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        [driver] = provider.drivers
        driver._state[("test.set_frequency/v1", "frequency")] = Quantity(
            value=5.0,
            unit="GHz",
        )
        driver.fail_action = "reject_apply"

        receipt = instruments.finish_run_hardware(
            run_id,
            RunHardwareFinishCommand(
                lease_id=lease_id,
                operation_id="hardware.finish",
                failed=False,
            ),
        )

        assert [item.code for item in receipt.problems] == ["test_apply_rejected"]
        assert driver.abort_count == 1
        [safe_patch] = driver.applied
        assert {
            target.property_id: value for target, value in safe_patch.values.items()
        } == {"frequency": Quantity(value=4.25, unit="GHz")}
        [final] = receipt.final_state
        frequency = next(
            item.value.root
            for item in final.observations
            if item.target.property_id == "frequency"
        )
        assert frequency == Quantity(value=4.25, unit="GHz")


def test_unknown_baseline_restore_quarantines_the_run(tmp_path: Path) -> None:
    provider = _Provider()
    config = _config_with_success_action(
        load_config(),
        success_action="restore_baseline",
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(runtime, config)
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))
        [driver] = provider.drivers
        driver._state[("test.set_frequency/v1", "frequency")] = Quantity(
            value=5.0,
            unit="GHz",
        )
        driver.fail_action = "apply"

        with pytest.raises(
            BackendConflict,
            match="baseline restore failed with unknown state",
        ):
            instruments.finish_run_hardware(
                run_id,
                RunHardwareFinishCommand(
                    lease_id=lease_id,
                    operation_id="hardware.finish",
                    failed=False,
                ),
            )

        assert driver.abort_count == 1
        assert driver.disconnect_count == 1
        durable = runtime.application.executor._control.get_run(run_id)
        assert durable.state == "attention_required"
        assert durable.attention_reason == "run_instrument_baseline_restore_unknown"
        _assert_run_state_discarded(instruments, run_id)


def test_rejected_failure_safe_state_is_reported_and_released(
    tmp_path: Path,
) -> None:
    provider = _Provider(fail_action="reject_apply")
    config = _config_with_safe_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=4.25, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(runtime, config)
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))

        receipt = instruments.finish_run_hardware(
            run_id,
            RunHardwareFinishCommand(
                lease_id=lease_id,
                operation_id="hardware.finish",
                failed=True,
            ),
        )

        assert [item.code for item in receipt.problems] == ["test_apply_rejected"]
        [driver] = provider.drivers
        assert driver.abort_count == 1
        assert runtime.application.executor._control.get_run(run_id).state != (
            "attention_required"
        )


def test_unknown_failure_safe_state_quarantines_the_run(tmp_path: Path) -> None:
    provider = _Provider(fail_action="apply")
    config = _config_with_safe_state(
        _setting(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=StateValue(Quantity(value=4.25, unit="GHz")),
        )
    )
    with _runtime(tmp_path, provider, config=config) as runtime:
        run_id, lease_id = _start_run(runtime, config)
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))

        with pytest.raises(BackendConflict, match="safe-state recovery failed"):
            instruments.finish_run_hardware(
                run_id,
                RunHardwareFinishCommand(
                    lease_id=lease_id,
                    operation_id="hardware.finish",
                    failed=True,
                ),
            )

        [driver] = provider.drivers
        assert driver.abort_count == 1
        assert driver.disconnect_count == 1
        durable = runtime.application.executor._control.get_run(run_id)
        assert durable.state == "attention_required"
        assert durable.attention_reason == "run_instrument_safe_state_unknown"
        _assert_run_state_discarded(instruments, run_id)


def test_analysis_candidate_run_keeps_connection_until_shutdown(
    tmp_path: Path,
) -> None:
    provider = _Provider(fail_action="disconnect")
    config = load_config()
    with _runtime(tmp_path, provider) as runtime:
        active = runtime.application.config.get_active_config()
        source_admission = runtime.application.submit_run(
            RunSubmission(
                submission_id="candidate-source",
                config=config,
                config_source=ConfigRegistryRunConfigSource(
                    selector="active",
                    entry_id=active.entry.id,
                    config_ref=active.entry.config_ref,
                    content_hash=active.entry.content_hash,
                    registry_generation=active.activation.generation,
                ),
                request=RunRequest(experiment_id="candidate-source"),
                plan=RunPlanSummary(
                    experiment_id="candidate-source",
                    experiment_kind="scratch",
                    point_plan_fingerprint="a" * 64,
                    measurement_contract_fingerprint="b" * 64,
                    point_count=1,
                    initial_point_count=1,
                    point_limit=1,
                ),
            )
        )
        proposal = parameter_change_proposal_from_updates(
            source_run_id=source_admission.run_id,
            source_config=config,
            analysis_title="candidate",
            analysis_record_id="analysis-candidate-r1",
            proposal_id="proposal-candidate",
            updates=(
                replace_scalar_parameter(
                    "drive_frequency",
                    Quantity(value=5.1, unit="GHz"),
                ),
            ),
            reason="candidate",
            confidence=None,
        )
        runtime.application.runs.save_run_analysis(
            source_admission.run_id,
            AnalysisSaveCommand(
                title="candidate",
                analysis_key="candidate",
                outputs=(
                    AnalysisParameterProposalOutputPayload(
                        kind="parameter_change_proposal",
                        id=proposal.id,
                        title="candidate",
                        content=proposal,
                    ),
                ),
            ),
        )
        candidate = resolve_candidate_config_from_snapshot(
            CandidateConfig(parameter_proposal=proposal),
            source_config=config,
        )
        source = AnalysisCandidateRunConfigSource(
            source_run_id=source_admission.run_id,
            analysis_record_id=proposal.analysis_record_id,
            proposal_id=proposal.id,
            base_config_content_hash=proposal.base_config_content_hash,
            content_hash=config_content_hash(candidate),
        )
        run_id, lease_id = _start_run(
            runtime,
            candidate,
            config_source=source,
            submission_id="analysis-candidate",
        )
        instruments = runtime.application.instruments
        instruments.provision_run(run_id, _provision(lease_id))

        receipt = instruments.finish_run_hardware(
            run_id,
            RunHardwareFinishCommand(
                lease_id=lease_id,
                operation_id="hardware.finish",
                failed=False,
            ),
        )

        [driver] = provider.drivers
        assert receipt.final_state[0].instrument_id == "source-0"
        assert driver.disconnect_count == 0
        assert runtime.application.executor._control.get_run(run_id).state != (
            "attention_required"
        )

    assert driver.disconnect_count == 1


def test_terminal_commit_uses_the_same_abort_finalizer_as_explicit_finish(
    tmp_path: Path,
) -> None:
    provider = _Provider()
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, load_config())
        runtime.application.instruments.provision_run(run_id, _provision(lease_id))
        runtime.application.executor.advance_run_coverage(
            run_id,
            RunCoverageAdvanceCommand(
                lease_id=lease_id,
                start_index=0,
                point_count=1,
            ),
        )

        manifest = runtime.application.executor.commit_terminal(
            run_id,
            TerminalRunCommitCommand(
                lease_id=lease_id,
                outcome=RunOutcome(
                    run_id=run_id,
                    result="succeeded",
                    certainty="known",
                ),
            ),
        )

        [driver] = provider.drivers
        assert manifest.outcome is not None
        assert driver.abort_count == 1
        assert driver.read_count == 2
        assert driver.disconnect_count == 0
        _assert_run_state_discarded(runtime.application.instruments, run_id)


def test_disjoint_runs_do_not_serialize_hardware_batches(tmp_path: Path) -> None:
    provider = _Provider(apply_barrier=Barrier(2))
    config = _two_instrument_config()
    with _runtime(tmp_path, provider, config=config) as runtime:
        first = _start_run(
            runtime,
            config,
            host_instrument_order=("source-0",),
            submission_id="first",
        )
        second = _start_run(
            runtime,
            config,
            host_instrument_order=("source-1",),
            submission_id="second",
        )
        instruments = runtime.application.instruments
        for run_id, lease_id in (first, second):
            instruments.provision_run(run_id, _provision(lease_id))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    instruments.execute_run_hardware,
                    run_id,
                    _batch_command(
                        lease_id,
                        f"batch-{index}",
                        _apply_action(
                            f"source-{index}",
                            effect_id=f"apply-{index}",
                        ),
                    ),
                )
                for index, (run_id, lease_id) in enumerate((first, second))
            ]
            assert all(not future.result(timeout=3).problems for future in futures)


def test_expiry_and_shutdown_fault_live_run_connections(tmp_path: Path) -> None:
    provider = _Provider()
    runtime = _runtime(tmp_path, provider)
    run_id, lease_id = _start_run(runtime, load_config())
    runtime.application.instruments.provision_run(run_id, _provision(lease_id))
    lease = runtime.application.executor._control.validate_executor_lease(
        run_id,
        token=lease_id,
    )
    expired = runtime.application.executor._control.expire_executor_leases(
        at=lease.expires_at,
    )
    runtime.application.instruments.expire_runs(expired)
    [driver] = provider.drivers
    assert driver.abort_count == 1
    assert driver.disconnect_count == 1
    runtime.close()

    shutdown_provider = _Provider()
    shutdown = _runtime(tmp_path / "shutdown", shutdown_provider)
    shutdown_run, shutdown_lease = _start_run(shutdown, load_config())
    shutdown.application.instruments.provision_run(
        shutdown_run,
        _provision(shutdown_lease),
    )
    shutdown.close()
    [shutdown_driver] = shutdown_provider.drivers
    assert shutdown_driver.abort_count == 1
    assert shutdown_driver.disconnect_count == 1


def test_expired_run_attempts_configured_safe_operation_before_disconnect(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_SafeOperationDriver)
    config = _config_with_safe_operation(
        failure_action="abort_then_safe_state",
        required=True,
    )
    runtime = _runtime(tmp_path, provider, config=config)
    run_id, lease_id = _start_run(
        runtime,
        config,
        driver_type=_SafeOperationDriver,
    )
    runtime.application.instruments.provision_run(run_id, _provision(lease_id))
    lease = runtime.application.executor._control.validate_executor_lease(
        run_id,
        token=lease_id,
    )
    expired = runtime.application.executor._control.expire_executor_leases(
        at=lease.expires_at,
    )

    runtime.application.instruments.expire_runs(expired)

    [driver] = provider.drivers
    assert driver.abort_count == 1
    [invocation] = driver.invoked
    assert invocation.target.interface_id == "test.safe_state/v1"
    assert driver.disconnect_count == 1
    runtime.close()


def test_shutdown_attempts_configured_safe_operation_before_disconnect(
    tmp_path: Path,
) -> None:
    provider = _Provider(driver_type=_SafeOperationDriver)
    config = _config_with_safe_operation(
        failure_action="abort_then_safe_state",
        required=True,
    )
    runtime = _runtime(tmp_path, provider, config=config)
    run_id, lease_id = _start_run(
        runtime,
        config,
        driver_type=_SafeOperationDriver,
    )
    runtime.application.instruments.provision_run(run_id, _provision(lease_id))

    runtime.close()

    [driver] = provider.drivers
    assert driver.abort_count == 1
    [invocation] = driver.invoked
    assert invocation.target.interface_id == "test.safe_state/v1"
    assert driver.disconnect_count == 1


def test_run_without_claims_does_not_build_provider(tmp_path: Path) -> None:
    provider = _Provider()
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(
            runtime,
            load_config(),
            host_instrument_order=(),
        )
        receipt = runtime.application.instruments.provision_run(
            run_id,
            _provision(lease_id),
        )
        assert receipt.status == "ready"
        assert receipt.observed_state == ()
        assert receipt.baseline_state == ()
        assert provider.connect_count == 0


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


def _config_with_safe_state(
    *properties: InstrumentStateSetting,
) -> ConfigProfileSnapshot:
    config = load_config()
    [instrument] = config.instrument_registry.instruments
    configured = instrument.model_copy(
        update={
            "safe_state": list(properties),
            "failure_action": "abort_then_safe_state",
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


def _config_with_safe_operation(
    config: ConfigProfileSnapshot | None = None,
    *,
    success_action: InstrumentSuccessAction = "release",
    failure_action: InstrumentFailureAction = "abort_and_release",
    required: bool = False,
) -> ConfigProfileSnapshot:
    config = config or load_config()
    [instrument] = config.instrument_registry.instruments
    configured = instrument.model_copy(
        update={
            "success_action": success_action,
            "failure_action": failure_action,
            "safe_operations": [
                InstrumentSafeOperation(
                    interface_id="test.safe_state/v1",
                    operation_id="enter",
                )
            ],
            "safe_state_requirement": "required" if required else "best_effort",
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


def _config_with_success_action(
    config: ConfigProfileSnapshot,
    *,
    success_action: InstrumentSuccessAction,
) -> ConfigProfileSnapshot:
    [instrument] = config.instrument_registry.instruments
    configured = instrument.model_copy(update={"success_action": success_action})
    registry = config.instrument_registry.model_copy(
        update={"instruments": [configured]}
    )
    return config.model_copy(
        update={
            "system": config.system.model_copy(update={"instrument_registry": registry})
        }
    )


def _two_instrument_config_with_default_state(
    *properties: InstrumentStateSetting,
) -> ConfigProfileSnapshot:
    config = _two_instrument_config()
    instruments = [
        instrument.model_copy(
            update={
                "run_start": "apply_default_state",
                "default_state": [item.model_copy(deep=True) for item in properties],
            }
        )
        for instrument in config.instrument_registry.instruments
    ]
    registry = config.instrument_registry.model_copy(
        update={"instruments": instruments}
    )
    return config.model_copy(
        update={
            "system": config.system.model_copy(update={"instrument_registry": registry})
        }
    )


def _runtime(
    root: Path,
    provider: _Provider,
    *,
    config: ConfigProfileSnapshot | None = None,
    lease_ttl: timedelta | None = None,
) -> LocalDaemonRuntime:
    return LocalDaemonRuntime(
        root,
        bootstrap_config=config or load_config(),
        instrument_endpoint=LocalInstrumentBackendEndpoint(
            InstrumentBackend(
                provider=provider,
                driver_catalog=DriverCatalog(provider_id=provider.provider_id),
                payload_codecs=json_payload_codecs("pulse_program"),
            )
        ),
        lease_ttl=lease_ttl,
    )


def _start_run(
    runtime: LocalDaemonRuntime,
    config: ConfigProfileSnapshot,
    *,
    host_instrument_order: tuple[str, ...] = ("source-0",),
    submission_id: str = "run-instruments",
    contract_fingerprint: str | None = None,
    driver_type: type[_Driver] = _Driver,
    config_source: RunConfigSource | Literal["matching_active"] | None = (
        "matching_active"
    ),
) -> tuple[str, str]:
    if config_source == "matching_active":
        active = runtime.application.config.get_active_config()
        selected_config_source = (
            ConfigRegistryRunConfigSource(
                selector="active",
                entry_id=active.entry.id,
                config_ref=active.entry.config_ref,
                content_hash=active.entry.content_hash,
                registry_generation=active.activation.generation,
            )
            if active.entry.content_hash == config_content_hash(config)
            else None
        )
    else:
        selected_config_source = config_source
    admitted_fingerprint = (
        instrument_contract_fingerprint(
            _Provider.provider_id,
            tuple(
                driver_type(instrument_id).describe()
                for instrument_id in host_instrument_order
            ),
        )
        if host_instrument_order
        else None
    )
    admission = runtime.application.submit_run(
        RunSubmission(
            submission_id=submission_id,
            config=config,
            config_source=selected_config_source,
            request=RunRequest(experiment_id="scratch"),
            plan=RunPlanSummary(
                experiment_id="scratch",
                experiment_kind="scratch",
                point_plan_fingerprint="a" * 64,
                measurement_contract_fingerprint="b" * 64,
                point_count=1,
                initial_point_count=1,
                point_limit=1,
                host_instrument_order=host_instrument_order,
                host_provider_id=(
                    _Provider.provider_id if host_instrument_order else None
                ),
                host_contract_fingerprint=(
                    contract_fingerprint or admitted_fingerprint
                ),
                run_resource_requirements=tuple(
                    RunResourceRequirement(kind="instrument", id=instrument_id)
                    for instrument_id in host_instrument_order
                ),
            ),
        )
    )
    lease = runtime.application.executor.start_executor(
        admission.run_id,
        ExecutorStartRequest(executor_id=f"executor-{submission_id}"),
    )
    return admission.run_id, lease.lease_id


def _provision(lease_id: str) -> RunInstrumentProvisionCommand:
    return RunInstrumentProvisionCommand(
        lease_id=lease_id,
        operation_id="hardware.provision",
    )


def _batch_command(
    lease_id: str,
    operation_id: str,
    *actions: RunHardwareApply | RunHardwareInvoke | RunHardwareCollect,
    sequence: int = 0,
) -> RunHardwareBatchCommand:
    return RunHardwareBatchCommand(
        lease_id=lease_id,
        sequence=sequence,
        batch=RunHardwareBatch(
            operation_id=operation_id,
            actions=actions,
        ),
    )


def _apply_action(
    instrument_id: str,
    *,
    effect_id: str,
) -> RunHardwareApply:
    return RunHardwareApply(
        effect_id=effect_id,
        point_index=0,
        instrument_id=instrument_id,
        assignments=(
            InstrumentStateAssignment(
                resource_id=instrument_id,
                target=state_member_target(
                    InterfaceRef("test.set_frequency/v1").property("frequency")
                ),
                value=StateValue(Quantity(value=5.0, unit="GHz")),
            ),
        ),
    )


def _variant_apply_action(
    *,
    effect_id: str,
    mode: str | None = None,
    voltage_level: float | None = None,
    current_level: float | None = None,
    output_enabled: bool | None = None,
) -> RunHardwareApply:
    values = {
        "mode": mode,
        "voltage_level": voltage_level,
        "current_level": current_level,
        "output_enabled": output_enabled,
    }
    return RunHardwareApply(
        effect_id=effect_id,
        point_index=0,
        instrument_id="source-0",
        assignments=tuple(
            InstrumentStateAssignment(
                resource_id="source-0",
                target=state_member_target(_DC.property(property_id)),
                value=StateValue(value),
            )
            for property_id, value in values.items()
            if value is not None
        ),
    )


def _state_sized_axis_apply_action(
    *,
    effect_id: str,
    points: int,
) -> RunHardwareApply:
    return RunHardwareApply(
        effect_id=effect_id,
        point_index=0,
        instrument_id="source-0",
        assignments=(
            InstrumentStateAssignment(
                resource_id="source-0",
                target=state_member_target(_SWEEP_POINTS),
                value=StateValue(points),
            ),
        ),
    )


def _state_sized_axis_collect_action(
    *,
    effect_id: str,
    size: int,
) -> RunHardwareCollect:
    return RunHardwareCollect(
        effect_id=effect_id,
        point_index=0,
        instrument_id="source-0",
        point_count=1,
        requests=(
            CollectResultRequest(
                id="trace",
                interface_id="test.sweep/v1",
                acquisition_id="sample",
                result_id="trace",
                unit="V",
                dimensions=[
                    CollectAxisRequest(
                        id="sample",
                        kind="sample",
                        size=size,
                    )
                ],
            ),
        ),
        bindings=(
            RunHardwareCollectBinding(
                request_id="trace",
                value_ids=(f"{effect_id}-use",),
            ),
        ),
    )


def _invoke_action(
    instrument_id: str,
    *,
    effect_id: str,
    payload: CommandPayload,
) -> RunHardwareInvoke:
    return RunHardwareInvoke(
        effect_id=effect_id,
        point_index=0,
        instrument_id=instrument_id,
        resource_id=instrument_id,
        interface_id="test.play_program/v1",
        operation_id="play",
        arguments=(
            InstrumentOperationArgument(
                id="program",
                value=StateValue(PayloadRef(payload_id=payload.id)),
            ),
        ),
        payloads={payload.id: payload},
    )


def _collect_action(
    instrument_id: str,
    *,
    effect_id: str,
) -> RunHardwareCollect:
    return RunHardwareCollect(
        effect_id=effect_id,
        point_index=0,
        instrument_id=instrument_id,
        point_count=1,
        requests=(
            CollectResultRequest(
                id="signal",
                interface_id="test.scalar_signal/v1",
                acquisition_id="sample",
                result_id="signal",
                unit="ratio",
            ),
        ),
        bindings=(
            RunHardwareCollectBinding(
                request_id="signal",
                value_ids=("signal-use",),
            ),
        ),
    )


def _variant_collect_action(
    *,
    effect_id: str,
    result_id: str,
) -> RunHardwareCollect:
    unit = "V" if result_id == "monitored_voltage" else "A"
    return RunHardwareCollect(
        effect_id=effect_id,
        point_index=0,
        instrument_id="source-0",
        point_count=1,
        requests=(
            CollectResultRequest(
                id="reading",
                interface_id="test.dc/v1",
                acquisition_id="measure",
                result_id=result_id,
                unit=unit,
            ),
        ),
        bindings=(
            RunHardwareCollectBinding(
                request_id="reading",
                value_ids=("reading-use",),
            ),
        ),
    )


def _assert_run_state_discarded(
    instruments: InstrumentService,
    run_id: str,
) -> None:
    assert run_id not in instruments._run_contexts


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


def test_release_rejects_admitted_run_before_and_after_provision(
    tmp_path: Path,
) -> None:
    provider = _Provider()
    with _runtime(tmp_path, provider) as runtime:
        run_id, lease_id = _start_run(runtime, load_config())
        instruments = runtime.application.instruments
        command = InstrumentReleaseCommand(instrument_ids=("source-0",))
        with pytest.raises(BackendConflict, match="idle devices"):
            instruments.release_idle_instruments(command)
        assert not provider.drivers
        instruments.provision_run(run_id, _provision(lease_id))
        with pytest.raises(BackendConflict, match="idle devices"):
            instruments.release_idle_instruments(command)
        assert provider.drivers[0].disconnect_count == 0
