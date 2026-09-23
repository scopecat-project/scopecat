from __future__ import annotations

from typing import override

import pytest
from scopecat_testkit.instrument_drivers import SignalInstrumentDriver
from scopecat_testkit.instrument_host import TestRunInstrumentHost
from scopecat_testkit.local_materialization import LocalEffectInspection
from scopecat_testkit.run_operations import complete_coverage_operations

from scopecat.execution.effect_interpreter import RunEffectInterpreter
from scopecat.execution.local.program import (
    ApplyStateOperation,
    CollectionResultBinding,
    CollectOperation,
    ComputeOperation,
    OutputInput,
    ResourceProvenance,
    StateDemandOrigin,
    StateTarget,
)
from scopecat.execution.program import RunCoverageCheckpoint
from scopecat.kernel.point_identity import LogicalPointId, PointDomainId
from scopecat.kernel.points import AcceptedRunPoint
from scopecat.kernel.problems import (
    ProblemPhase,
    model_location,
    problem,
)
from scopecat.kernel.product_identity import ProductUse, ProductUseId, product_id
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.resource_identity import (
    DEFAULT_RESOURCE_ROLE,
    ResourceRequirement,
    logical_resource_port_id,
)
from scopecat.kernel.state import StateValue
from scopecat.kernel.symbols import SymbolId
from scopecat.kernel.value_types import Float, Scalar
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.measurements.records import ValueRecordCandidate
from scopecat.measurements.values import MeasurementValueCandidate
from scopecat.program.value_graph import (
    ComputeOutput,
    OperationId,
    operation_result_id,
)
from scopecat.records.instrument import (
    InstrumentStateSnapshot,
    state_member_identity,
)
from scopecat.records.measurement import MeasurementScalar
from scopecat.records.parameter_read import (
    HostParameterEvidence,
    HostSuccessStateParameterRead,
    ScalarExpressionReadEvidence,
)
from scopecat.sdk.domain.execution import DomainResidencyAddress
from scopecat.sdk.instruments import (
    DriverAcquisition,
    DriverOutcome,
    DriverReadback,
    DriverRejected,
    DriverStatePatch,
    DriverStateReadback,
    DriverStateReadRequest,
    DriverSuccess,
    DriverUnknown,
)
from scopecat.sdk.instruments.commands import CollectCommand, CollectResultRequest


def _logical_point_id(name: str, ordinal: int = 0) -> LogicalPointId:
    return LogicalPointId(PointDomainId(name, "root"), ordinal)


def _requirements(*instrument_ids: str) -> tuple[ResourceRequirement, ...]:
    return tuple(
        ResourceRequirement(id=instrument_id) for instrument_id in instrument_ids
    )


def _provenance(instrument_id: str) -> ResourceProvenance:
    return ResourceProvenance(
        logical_port_id=logical_resource_port_id(instrument_id),
        requested_role=DEFAULT_RESOURCE_ROLE,
        route_id=instrument_id,
        route_role_id=None,
    )


def _state_origin(instrument_id: str) -> StateDemandOrigin:
    return StateDemandOrigin(resource=_provenance(instrument_id))


def _state_values(
    states: tuple[InstrumentStateSnapshot, ...],
) -> dict[str, dict[object, StateValue]]:
    return {
        state.instrument_id: {
            state_member_identity(observation.target): observation.value
            for observation in state.observations
        }
        for state in states
    }


def test_coverage_iterator_is_consumed_after_each_checkpoint() -> None:
    delivered: list[tuple[int, ...]] = []
    points = tuple(
        AcceptedRunPoint(_logical_point_id("incremental-source", ordinal), {})
        for ordinal in range(2)
    )

    def operations():
        yield RunCoverageCheckpoint("point:0", (0,))
        assert delivered == [(0,)]
        yield RunCoverageCheckpoint("point:1", (1,))

    result = RunEffectInterpreter(
        run_id="incremental-source-run",
        coordinate_ids=(),
        instruments=TestRunInstrumentHost(),
        coverage_observer=(
            lambda _group, selected, _candidates, _values: delivered.append(
                tuple(point.ordinal for point in selected)
            )
        ),
    ).run(operations(), points=points)

    assert not result.problems
    assert delivered == [(0,), (1,)]


@pytest.mark.parametrize("fail_evidence", [False, True])
def test_normal_completion_applies_success_state_after_point_coverage(
    fail_evidence: bool,
) -> None:
    driver = SignalInstrumentDriver(instrument_id="source-0")
    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id("success_state-point"), {}),
        (_gain_operation("source-0", 1.0),),
        resource_order=("source-0",),
        resource_requirements=_requirements("source-0"),
    )

    evidence = HostParameterEvidence(
        success_state=HostSuccessStateParameterRead(
            evidence=ScalarExpressionReadEvidence()
        ),
        binding=(),
    )

    def publish(value: HostParameterEvidence) -> None:
        assert value == evidence
        assert len(driver.applied) == 1
        if fail_evidence:
            raise RuntimeError("success evidence write failed")

    result = RunEffectInterpreter(
        run_id="success_state-run",
        coordinate_ids=(),
        instruments=TestRunInstrumentHost((driver,)),
        publish_host_parameter_evidence=publish,
    ).run(
        complete_coverage_operations(program),
        points=program.points,
        success_state=(_gain_operation("source-0", 0.0),),
        success_state_parameter_evidence=evidence,
    )

    if fail_evidence:
        assert len(driver.applied) == 1
        assert [problem.code for problem in result.problems] == [
            "run_effect_interpretation_failed"
        ]
        return

    assert not result.problems and not result.indeterminate
    assert len(driver.applied) == 2
    assert result.state_actions is not None
    assert result.state_actions.total_count == 2
    assert result.state_actions.detail_complete
    assert [
        (action.status, action.point_index, action.metadata)
        for action in result.state_actions.retained_prefix
    ] == [
        ("applied", 0, {}),
        ("applied", None, {}),
    ]
    [final] = result.final_state
    assert next(
        item.value
        for item in final.observations
        if item.target.kind == "interface"
        and item.target.interface_id == "test.set_gain/v1"
        and item.target.property_id == "gain"
    ) == StateValue(0.0)


def test_host_state_reconciliation_invalidates_only_affected_instrument_residency() -> (
    None
):
    driver = SignalInstrumentDriver(instrument_id="lo-source")
    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id("host-residency-point"), {}),
        (_gain_operation("lo-source", 1.0),),
        resource_order=("lo-source",),
        resource_requirements=_requirements("lo-source"),
    )
    engine = RunEffectInterpreter(
        run_id="host-residency-run",
        coordinate_ids=(),
        instruments=TestRunInstrumentHost((driver,)),
    )
    lo_residency = DomainResidencyAddress("lo-source", "program")
    controller_residency = DomainResidencyAddress("waveform-controller", "program")
    engine._domain_residency.contents = {
        lo_residency: "lo-program",
        controller_residency: "waveform-program",
    }

    result = engine.run(complete_coverage_operations(program), points=program.points)

    assert not result.problems
    assert engine._domain_residency.contents == {
        controller_residency: "waveform-program",
    }


def test_cancellation_waits_for_hardware_batch_then_skips_success_state() -> None:
    first = SignalInstrumentDriver(instrument_id="source-a")
    second = SignalInstrumentDriver(instrument_id="source-b")
    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id("cancel-batch-point"), {}),
        (
            _gain_operation("source-a", 1.0),
            _gain_operation("source-b", 2.0),
        ),
        resource_order=("source-a", "source-b"),
        resource_requirements=_requirements("source-a", "source-b"),
    )

    result = RunEffectInterpreter(
        run_id="cancel-batch-run",
        coordinate_ids=(),
        instruments=TestRunInstrumentHost((first, second)),
        cancellation_requested=lambda: bool(first.applied),
    ).run(
        complete_coverage_operations(program),
        points=program.points,
        success_state=(_gain_operation("source-a", 0.0),),
    )

    assert result.cancelled and not result.indeterminate
    assert [item.code for item in result.problems] == ["run_cancellation_requested"]
    assert len(first.applied) == 1
    assert len(second.applied) == 1


def test_compute_output_is_normalized_before_downstream_use() -> None:
    consumed: list[Quantity] = []
    producer_id = "normalized-output-point.compute.producer"
    producer_result_id = operation_result_id(OperationId(SymbolId(local_id="producer")))
    consumer_result_id = operation_result_id(OperationId(SymbolId(local_id="consumer")))

    def consume(*, value: Quantity) -> float:
        consumed.append(value)
        return value.value

    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id("normalized-output-point"), {}),
        (
            ComputeOperation(
                operation_id=producer_id,
                logical_compute_node_id="producer",
                implementation_id="python.producer.v1",
                kernel=lambda: Quantity(
                    value=5000.0,
                    unit="MHz",
                ),
                inputs={},
                result=ComputeOutput(
                    id=producer_result_id,
                    value_type=Scalar(QuantityType(unit="GHz")),
                ),
            ),
            ComputeOperation(
                operation_id=("normalized-output-point.compute.consumer"),
                logical_compute_node_id="consumer",
                implementation_id="python.consumer.v1",
                kernel=consume,
                inputs={
                    "value": OutputInput(
                        producer_result_id,
                        Scalar(QuantityType(unit="GHz")),
                    )
                },
                result=ComputeOutput(
                    id=consumer_result_id,
                    value_type=Scalar(Float()),
                ),
            ),
        ),
        resource_order=(),
        resource_requirements=(),
    )

    result = RunEffectInterpreter(
        run_id="normalized-output-run",
        coordinate_ids=tuple(program.points[0].coordinates),
        instruments=TestRunInstrumentHost(),
    ).run(complete_coverage_operations(program), points=program.points)

    assert not result.problems and not result.indeterminate
    assert consumed == [Quantity(value=5.0, unit="GHz")]


def test_recorded_compute_output_is_exported_before_point_state_is_closed() -> None:
    result_id = operation_result_id(OperationId(SymbolId(local_id="score")))
    point = AcceptedRunPoint(_logical_point_id("recorded-compute-point"), {})
    program = LocalEffectInspection.at_point(
        point,
        (
            ComputeOperation(
                operation_id="recorded-compute-point.compute.score",
                logical_compute_node_id="score",
                implementation_id="python.score.v1",
                kernel=lambda: 2.5,
                inputs={},
                result=ComputeOutput(
                    id=result_id,
                    value_type=Scalar(Float()),
                ),
            ),
        ),
        resource_order=(),
        resource_requirements=(),
    )
    observed: list[ValueRecordCandidate] = []

    result = RunEffectInterpreter(
        run_id="recorded-compute-run",
        coordinate_ids=(),
        instruments=TestRunInstrumentHost(),
        recorded_value_ids=(result_id,),
        coverage_observer=(
            lambda _group, _points, _products, values: observed.extend(values)
        ),
    ).run(complete_coverage_operations(program), points=program.points)

    assert not result.problems
    assert [(candidate.value_id, candidate.value) for candidate in observed] == [
        (result_id, 2.5)
    ]


def test_distinct_compute_operations_are_each_evaluated() -> None:
    calls: list[str] = []
    first_result_id = operation_result_id(OperationId(SymbolId(local_id="first")))
    second_result_id = operation_result_id(OperationId(SymbolId(local_id="second")))

    def first() -> float:
        calls.append("first")
        return 1.0

    def second() -> float:
        calls.append("second")
        return 2.0

    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id("implementation-cache-point"), {}),
        (
            ComputeOperation(
                operation_id="implementation-cache-point.compute.first",
                logical_compute_node_id="first",
                implementation_id="python.first.v1",
                kernel=first,
                inputs={},
                result=ComputeOutput(
                    id=first_result_id,
                    value_type=Scalar(Float()),
                ),
            ),
            ComputeOperation(
                operation_id="implementation-cache-point.compute.second",
                logical_compute_node_id="second",
                implementation_id="python.second.v1",
                kernel=second,
                inputs={},
                result=ComputeOutput(
                    id=second_result_id,
                    value_type=Scalar(Float()),
                ),
            ),
        ),
        resource_order=(),
        resource_requirements=(),
    )

    result = RunEffectInterpreter(
        run_id="implementation-cache-run",
        coordinate_ids=tuple(program.points[0].coordinates),
        instruments=TestRunInstrumentHost(),
    ).run(complete_coverage_operations(program), points=program.points)

    assert not result.problems and not result.indeterminate
    assert calls == ["first", "second"]


def test_compute_failure_wins_over_a_concurrent_cancellation_request() -> None:
    failed = False
    result_id = operation_result_id(OperationId(SymbolId(local_id="failing")))

    def fail() -> float:
        nonlocal failed
        failed = True
        raise RuntimeError("compute failed")

    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id("failing-compute-point"), {}),
        (
            ComputeOperation(
                operation_id="failing-compute-point.compute.failing",
                logical_compute_node_id="failing",
                implementation_id="python.failing.v1",
                kernel=fail,
                inputs={},
                result=ComputeOutput(
                    id=result_id,
                    value_type=Scalar(Float()),
                ),
            ),
        ),
        resource_order=(),
        resource_requirements=(),
    )

    result = RunEffectInterpreter(
        run_id="failing-compute-run",
        coordinate_ids=(),
        instruments=TestRunInstrumentHost(),
        cancellation_requested=lambda: failed,
    ).run(complete_coverage_operations(program), points=program.points)

    assert not result.cancelled
    assert [item.code for item in result.problems] == ["compute_operation_failed"]
    assert result.problems[0].message.endswith("(RuntimeError: compute failed)")


class _BlockingStateDriver(SignalInstrumentDriver):
    @override
    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.applied.append(request)
        return DriverRejected(
            problems=(
                problem(
                    "instrument_driver_blocked",
                    "driver blocked",
                    phase=ProblemPhase.EXECUTION,
                    location=model_location("instrument", self.instrument_id),
                ),
            ),
        )


class _NonConvergingStateDriver(SignalInstrumentDriver):
    @override
    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.applied.append(request)
        return DriverSuccess(None)


class _UnknownAppliedStateDriver(SignalInstrumentDriver):
    @override
    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        super().apply_state(request)
        return DriverUnknown(
            problems=(
                problem(
                    "instrument_driver_applied_with_error",
                    "driver reported an error after applying state",
                    phase=ProblemPhase.EXECUTION,
                    location=model_location("instrument", self.instrument_id),
                ),
            ),
        )


class _FinalizationTrackingDriver(SignalInstrumentDriver):
    def __init__(self, *, instrument_id: str) -> None:
        super().__init__(instrument_id=instrument_id)
        self.abort_count = 0
        self.disconnect_count = 0
        self.read_count_when_disconnected: int | None = None
        self.read_count = 0

    @override
    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        self.read_count += 1
        return super().read_state(request)

    @override
    def abort(self) -> None:
        self.abort_count += 1

    @override
    def disconnect(self) -> None:
        self.disconnect_count += 1
        self.read_count_when_disconnected = self.read_count


class _DisconnectFailureDriver(_FinalizationTrackingDriver):
    @override
    def disconnect(self) -> None:
        super().disconnect()
        raise RuntimeError("socket disconnect failed")


class _RejectOnSuccessStateDriver(_FinalizationTrackingDriver):
    @override
    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        if not self.applied:
            return super().apply_state(request)
        self.applied.append(request)
        return DriverRejected(
            problems=(
                problem(
                    "instrument_on_success_state_rejected",
                    "driver rejected normal-completion state",
                    phase=ProblemPhase.EXECUTION,
                    location=model_location("instrument", self.instrument_id),
                ),
            ),
        )


def test_rejected_on_success_state_aborts_hardware_finish() -> None:
    driver = _RejectOnSuccessStateDriver(instrument_id="source-0")
    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id("rejected-on-success-point"), {}),
        (_gain_operation("source-0", 1.0),),
        resource_order=("source-0",),
        resource_requirements=_requirements("source-0"),
    )

    result = RunEffectInterpreter(
        run_id="rejected-on-success-run",
        coordinate_ids=(),
        instruments=TestRunInstrumentHost((driver,)),
    ).run(
        complete_coverage_operations(program),
        points=program.points,
        success_state=(_gain_operation("source-0", 0.0),),
    )

    assert [item.code for item in result.problems] == [
        "instrument_on_success_state_rejected"
    ]
    assert not result.indeterminate
    assert len(driver.applied) == 2
    assert driver.abort_count == 1


def test_one_provider_readback_fans_out_to_every_logical_product_use() -> None:
    driver = SignalInstrumentDriver()
    point = AcceptedRunPoint(_logical_point_id("shared-readback-point"), {})
    uses = (
        _collection_product_use("first-signal-use"),
        _collection_product_use("second-signal-use"),
    )
    operation_id = "shared-readback-point.collect.source-0"
    operation = CollectOperation(
        operation_id=operation_id,
        instrument_id=driver.instrument_id,
        resource=_provenance(driver.instrument_id),
        command=CollectCommand(
            command_id=operation_id,
            instrument_id=driver.instrument_id,
            point_index=0,
            point_count=1,
            requests=[
                CollectResultRequest(
                    id="signal",
                    interface_id="test.scalar_signal/v1",
                    acquisition_id="sample",
                    result_id="signal",
                )
            ],
        ),
        result_bindings=(
            CollectionResultBinding(
                request_id="signal",
                product_use_ids=tuple(use.id for use in uses),
            ),
        ),
    )
    program = LocalEffectInspection.at_point(
        point,
        (operation,),
        resource_order=(driver.instrument_id,),
        resource_requirements=_requirements(driver.instrument_id),
    )
    observed_candidates: list[tuple[MeasurementValueCandidate, ...]] = []
    result = RunEffectInterpreter(
        run_id="shared-readback-run",
        coordinate_ids=tuple(point.coordinates),
        instruments=TestRunInstrumentHost((driver,)),
        coverage_observer=(
            lambda _group, _block, candidates, _values: observed_candidates.append(
                candidates
            )
        ),
    ).run(complete_coverage_operations(program), points=program.points)

    assert not result.problems and not result.indeterminate
    assert len(driver.collect_requests) == 1
    assert [result.result_id for result in driver.collect_requests[0].results] == [
        "signal"
    ]
    [candidates] = observed_candidates
    assert [
        (candidate.logical_point_id, candidate.product_use_id, candidate.value)
        for candidate in candidates
    ] == [
        (
            point.logical_id,
            use.id,
            MeasurementScalar.create(
                dtype="float64",
                value=1.0,
                unit="ratio",
            ),
        )
        for use in uses
    ]
    evidence = candidates[0].evidence
    assert evidence is not None
    assert evidence.command_id == operation_id
    assert evidence.instrument_id == driver.instrument_id
    assert evidence.acquisition_id == "sample"
    assert evidence.result_id == "signal"
    assert all(candidate.evidence == evidence for candidate in candidates)


def test_driver_disconnect_failure_is_reported_after_terminal_read() -> None:
    driver = _DisconnectFailureDriver(instrument_id="source-0")
    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id("disconnect-failure-point"), {}),
        (_gain_operation("source-0", 1.0),),
        resource_order=("source-0",),
        resource_requirements=_requirements("source-0"),
    )

    result = RunEffectInterpreter(
        run_id="disconnect-failure-run",
        coordinate_ids=tuple(program.points[0].coordinates),
        instruments=TestRunInstrumentHost((driver,)),
    ).run(complete_coverage_operations(program), points=program.points)

    assert driver.disconnect_count == 1
    assert driver.read_count_when_disconnected == 3
    assert "hardware_finalization_unknown" in {item.code for item in result.problems}


def test_state_apply_stops_on_blocking_result_without_committing_state() -> None:
    first = _BlockingStateDriver(instrument_id="source-a")
    second = SignalInstrumentDriver(instrument_id="source-b")
    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id("blocking-state-point"), {}),
        (
            _gain_operation("source-a", 1.0),
            _gain_operation("source-b", 2.0),
        ),
        resource_order=("source-a", "source-b"),
        resource_requirements=_requirements("source-a", "source-b"),
    )
    engine = RunEffectInterpreter(
        run_id="blocking-state-run",
        coordinate_ids=tuple(program.points[0].coordinates),
        instruments=TestRunInstrumentHost((first, second)),
    )

    result = engine.run(complete_coverage_operations(program), points=program.points)

    assert result.problems and not result.indeterminate
    assert [problem.code for problem in result.problems] == [
        "instrument_driver_blocked"
    ]
    assert len(first.applied) == 1
    assert second.applied == []
    assert _state_values(result.final_state) == _state_values(result.baseline_state)


def test_state_apply_stops_when_readback_does_not_confirm_assignment() -> None:
    first = _NonConvergingStateDriver(instrument_id="source-a")
    second = SignalInstrumentDriver(instrument_id="source-b")
    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id("non-converging-state-point"), {}),
        (
            _gain_operation("source-a", 1.0),
            _gain_operation("source-b", 2.0),
        ),
        resource_order=("source-a", "source-b"),
        resource_requirements=_requirements("source-a", "source-b"),
    )
    result = RunEffectInterpreter(
        run_id="non-converging-state-run",
        coordinate_ids=tuple(program.points[0].coordinates),
        instruments=TestRunInstrumentHost((first, second)),
    ).run(complete_coverage_operations(program), points=program.points)

    assert result.indeterminate
    assert [problem.code for problem in result.problems] == [
        "instrument_apply_state_mismatch"
    ]
    assert len(first.applied) == 1
    assert second.applied == []
    assert _state_values(result.final_state) == _state_values(result.baseline_state)


def test_failed_coverage_does_not_apply_normal_completion_success_state() -> None:
    driver = _BlockingStateDriver(instrument_id="source-0")
    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id("failed-success_state-point"), {}),
        (_gain_operation("source-0", 1.0),),
        resource_order=("source-0",),
        resource_requirements=_requirements("source-0"),
    )

    result = RunEffectInterpreter(
        run_id="failed-success_state-run",
        coordinate_ids=(),
        instruments=TestRunInstrumentHost((driver,)),
    ).run(
        complete_coverage_operations(program),
        points=program.points,
        success_state=(_gain_operation("source-0", 0.0),),
    )

    assert result.problems
    assert len(driver.applied) == 1


class _UnexpectedResultDriver(SignalInstrumentDriver):
    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        self.collect_requests.append(request)
        signal = request.target.result("signal")
        return DriverSuccess(
            DriverReadback(
                values={
                    signal: MeasurementScalar.create(
                        dtype="float64",
                        value=1.0,
                        unit="ratio",
                    ),
                    request.target.result("unexpected"): MeasurementScalar.create(
                        dtype="float64",
                        value=2.0,
                        unit="ratio",
                    ),
                }
            ),
        )


def test_unexpected_result_stops_later_collection() -> None:
    first = _UnexpectedResultDriver(instrument_id="source-a")
    second = SignalInstrumentDriver(instrument_id="source-b")
    point_uid = "blocking-collect-point"
    first_operation = _collect_operation(point_uid, "source-a", "first")
    second_operation = _collect_operation(point_uid, "source-b", "second")
    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id(point_uid), {}),
        (first_operation, second_operation),
        resource_order=("source-a", "source-b"),
        resource_requirements=_requirements("source-a", "source-b"),
    )
    result = RunEffectInterpreter(
        run_id="blocking-collect-run",
        coordinate_ids=tuple(program.points[0].coordinates),
        instruments=TestRunInstrumentHost((first, second)),
    ).run(complete_coverage_operations(program), points=program.points)

    assert result.problems and not result.indeterminate
    assert [problem.code for problem in result.problems] == [
        "instrument_unexpected_product"
    ]
    assert len(first.collect_requests) == 1
    assert second.collect_requests == []


def test_unknown_receipt_with_problem_does_not_advance_state() -> None:
    first = _UnknownAppliedStateDriver(instrument_id="source-a")
    second = SignalInstrumentDriver(instrument_id="source-b")
    program = LocalEffectInspection.at_point(
        AcceptedRunPoint(_logical_point_id("conflicting-applied-state-point"), {}),
        (
            ApplyStateOperation(
                operation_id=("conflicting-applied-state-point.state.source-a"),
                instrument_id="source-a",
                targets=(
                    StateTarget(
                        interface_id="test.set_gain/v1",
                        property_id="gain",
                        value=StateValue(1.0),
                        origins=(_state_origin("source-a"),),
                    ),
                ),
            ),
            ApplyStateOperation(
                operation_id=("conflicting-applied-state-point.state.source-b"),
                instrument_id="source-b",
                targets=(
                    StateTarget(
                        interface_id="test.set_gain/v1",
                        property_id="gain",
                        value=StateValue(2.0),
                        origins=(_state_origin("source-b"),),
                    ),
                ),
            ),
        ),
        resource_order=("source-a", "source-b"),
        resource_requirements=_requirements("source-a", "source-b"),
    )
    engine = RunEffectInterpreter(
        run_id="conflicting-applied-state-run",
        coordinate_ids=tuple(program.points[0].coordinates),
        instruments=TestRunInstrumentHost((first, second)),
    )

    result = engine.run(complete_coverage_operations(program), points=program.points)

    assert result.indeterminate
    assert [problem.code for problem in result.problems] == [
        "instrument_driver_applied_with_error",
    ]
    assert len(first.applied) == 1
    assert second.applied == []
    assert result.final_state[0] != result.baseline_state[0]
    assert next(
        item.value
        for item in result.final_state[0].observations
        if item.target.kind == "interface"
        and item.target.interface_id == "test.set_gain/v1"
        and item.target.property_id == "gain"
    ) == StateValue(1.0)


def _gain_operation(instrument_id: str, value: float) -> ApplyStateOperation:
    return ApplyStateOperation(
        operation_id=f"blocking-state-point.state.{instrument_id}",
        instrument_id=instrument_id,
        targets=(
            StateTarget(
                interface_id="test.set_gain/v1",
                property_id="gain",
                value=StateValue(value),
                origins=(_state_origin(instrument_id),),
            ),
        ),
    )


def _collect_operation(
    point_uid: str,
    instrument_id: str,
    output_id: str,
) -> CollectOperation:
    use = _collection_product_use(output_id)
    operation_id = f"{point_uid}.collect.{instrument_id}"
    return CollectOperation(
        operation_id=operation_id,
        instrument_id=instrument_id,
        resource=_provenance(instrument_id),
        command=CollectCommand(
            command_id=operation_id,
            instrument_id=instrument_id,
            point_index=0,
            point_count=1,
            requests=[
                CollectResultRequest(
                    id="signal",
                    interface_id="test.scalar_signal/v1",
                    acquisition_id="sample",
                    result_id="signal",
                )
            ],
        ),
        result_bindings=(
            CollectionResultBinding(
                request_id="signal",
                product_use_ids=(use.id,),
            ),
        ),
    )


def _collection_product_use(output_id: str) -> ProductUse:
    return ProductUse(
        product_id=product_id("signal"),
        id=ProductUseId(f"record:{output_id}"),
    )
