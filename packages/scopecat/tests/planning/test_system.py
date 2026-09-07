from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Annotated, Literal, Never, cast

import numpy as np
import pytest
from scopecat_testkit.authoring import load_config
from scopecat_testkit.bound_program import (
    DomainExecutionFixture,
    DomainResultFixture,
    bind_program_facts,
    instrument_acquisition,
    observable_product,
    overlay_parameter_cell,
    program_fixture,
)
from scopecat_testkit.expressions import state_property, verified_scalar_expr
from scopecat_testkit.parameter_fixtures import (
    READOUT_FREQUENCY_LOOKUP,
)
from scopecat_testkit.parameter_fixtures import (
    parameters as parameter_fixture_data,
)
from scopecat_testkit.signal_instruments import TestSignalInstrumentProvider

import scopecat as sc
from scopecat.compiler.bind import BoundPlan, bind_program
from scopecat.compiler.bound_facts import (
    BoundMeasurementCompute,
    BoundMeasurementComputeInput,
    BoundMeasurementComputeOutput,
    LogicalResourceRequirement,
    record_product,
)
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.compiler.parameter_overlays import PointParameterOverlay
from scopecat.compiler.point_domain import PointDomain
from scopecat.compiler.relations.context import ParameterRelationData
from scopecat.compiler.relations.verification import (
    ExpressionTypeBindings,
    RowType,
)
from scopecat.config.environment import build_config_environment
from scopecat.domain.program import (
    DomainInputPort,
    DomainProgramDef,
    DomainResultPort,
)
from scopecat.execution.local.program import (
    ApplyStateOperation,
    CollectOperation,
    ComputeOperation,
)
from scopecat.execution.program import (
    RunCoverageCheckpoint,
    RunCoverageEffect,
    RunCoveredOperation,
    RunDomainJob,
)
from scopecat.kernel.errors import CheckFailed, ProviderContractError
from scopecat.kernel.instrument_members import InterfaceRef
from scopecat.kernel.point_identity import LogicalPointId
from scopecat.kernel.points import PointProposalAttempt, PointProposalSource
from scopecat.kernel.problems import ProblemPhase, problem
from scopecat.kernel.product_identity import product_use
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.resource_identity import (
    DomainTargetRequirement,
    ResourceRequirement,
    logical_resource_port_id,
)
from scopecat.kernel.state import StateValue
from scopecat.kernel.symbols import SymbolId
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_types import Scalar, Table, TableColumn
from scopecat.measurements.results import MeasurementValue
from scopecat.optimization import (
    AdaptiveDomainPlan,
    DomainOptimizerContext,
    OptimizationComplete,
)
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.local_effects import LocalTargetPlan, MaterializedLocalEffects
from scopecat.planning.local_materialization import materialize_local_execution
from scopecat.planning.point_materialization import MaterializedBoundPoints
from scopecat.planning.preview import build_run_program_preview
from scopecat.planning.provider_binding import (
    resolve_instrument_contract_catalog,
)
from scopecat.planning.system import ExperimentSystem, build_experiment_system
from scopecat.program.expressions import (
    ScalarExpr,
    lit,
    parameter_lookup,
    point_col,
)
from scopecat.program.logical import (
    MeasurementComputeId,
)
from scopecat.program.point_domain import point_axis_values
from scopecat.program.scans import PointGrouping
from scopecat.records.config import (
    ConfigProfileSnapshot,
    DomainTargetBinding,
    config_content_hash,
)
from scopecat.sdk.domain import (
    DomainBatchCandidate,
    DomainBatchPreparationCost,
    DomainBatchPreparationLimits,
    DomainBatchRequest,
    DomainPreparationBuilder,
    DomainStateAddress,
    DomainStateRequirement,
)
from scopecat.sdk.domain.execution import (
    PreparedDomainExecution,
)
from scopecat.sdk.domain.job import (
    DomainInvocationSpec,
    DomainResultValue,
)
from scopecat.sdk.domain.result_mapping import (
    DomainResultBinding,
)
from scopecat.sdk.domain.runtime import (
    DomainExecutionReceipt,
    DomainExecutionResult,
)
from scopecat.sdk.instruments import (
    InstrumentConnectionContext,
    InstrumentProvider,
    InstrumentProviderContext,
    InstrumentProviderDescription,
)


class _CompleteOptimizer:
    id = "tests.complete"

    def propose(
        self,
        context: DomainOptimizerContext,
    ) -> sc.DomainProposalAttempt | OptimizationComplete:
        del context
        return OptimizationComplete()


class _EffectProbeRuntime:
    def __init__(self) -> None:
        self.start_calls = 0

    def start(
        self,
        execution_key: str,
        payload: dict[str, str],
        *,
        instruments: object,
    ) -> DomainExecutionReceipt | DomainExecutionResult[dict[str, str]]:
        del execution_key, payload, instruments
        self.start_calls += 1
        raise AssertionError("planning must not execute a domain invocation")


@dataclass
class _DomainCompiler:
    compiler_id: str
    instrument_ids: tuple[str, ...] = ()
    state_requirements: tuple[DomainStateRequirement, ...] = ()
    realtime_write_footprint: tuple[DomainStateAddress, ...] = ()
    realtime_write_batches: frozenset[int] | None = None
    realtime_state_invalidations: tuple[DomainStateAddress, ...] = ()
    batch_size: int = 100
    initial_size: int | None = None
    compatible_sizes: tuple[int, ...] | None = None
    next_batch_capacities: tuple[int, ...] | None = None
    max_retained_bytes: int = 1024 * 1024
    retained_bytes: int = 0
    analyzed_point_count: int | None = None
    job_runtime: _EffectProbeRuntime = field(default_factory=_EffectProbeRuntime)
    initial_batch_requests: list[int] = field(default_factory=list)
    compatibility_requests: list[DomainBatchRequest] = field(default_factory=list)
    compile_calls: int = 0
    compile_requests: list[DomainBatchRequest] = field(default_factory=list)
    prepared_inputs: list[tuple[object, ...]] = field(default_factory=list)

    @property
    def target_id(self) -> str:
        return "tests.domain.target"

    @property
    def target_kind(self) -> str:
        return "tests.domain"

    def initial_batch_preparation_limits(
        self,
        point_count: int,
    ) -> DomainBatchPreparationLimits:
        self.initial_batch_requests.append(point_count)
        return DomainBatchPreparationLimits(
            max_points=(
                min(point_count, self.batch_size)
                if self.initial_size is None
                else self.initial_size
            ),
            max_retained_bytes=self.max_retained_bytes,
        )

    def prepare_batch(self, request: DomainBatchRequest) -> DomainBatchCandidate:
        index = len(self.compatibility_requests)
        self.compatibility_requests.append(request)
        if self.compatible_sizes is None:
            compatible_point_count = len(request.points)
        else:
            compatible_point_count = self.compatible_sizes[
                min(index, len(self.compatible_sizes) - 1)
            ]
        return DomainBatchCandidate(
            compatible_point_count=compatible_point_count,
            preparation_cost=DomainBatchPreparationCost(
                analyzed_point_count=(
                    len(request.points)
                    if self.analyzed_point_count is None
                    else self.analyzed_point_count
                ),
                retained_bytes=self.retained_bytes,
            ),
            _compile=self._compile_batch,
        )

    def _compile_batch(
        self,
        request: DomainBatchRequest,
    ) -> PreparedDomainExecution:
        self.compile_calls += 1
        self.compile_requests.append(request)
        if request.inputs.program:
            self.prepared_inputs.append(
                request.inputs.program[0][1],
            )
        preparation = DomainPreparationBuilder(request)
        product_uses = request.product_uses
        result_addresses = tuple(
            tuple(
                f"{self.compiler_id}.result.{point.ordinal}.{use_index}"
                for use_index in range(len(product_uses))
            )
            for point in request.points
        )
        mapping = preparation.map_measurements(
            results=tuple(
                DomainResultBinding(
                    result_address,
                    point,
                    product_uses[use_index],
                )
                for point, addresses in zip(
                    request.points, result_addresses, strict=True
                )
                for use_index, result_address in enumerate(addresses)
            ),
        )
        invocation = DomainInvocationSpec(
            invocation_id=(
                f"{self.compiler_id}.invocation.batch-{request.batch_ordinal}"
            ),
            target_id=self.target_id,
            compiler_id=self.compiler_id,
            capability_fingerprint=f"{self.compiler_id}.interfaces",
            artifact_id=(f"{self.compiler_id}.artifact.batch-{request.batch_ordinal}"),
            artifact_fingerprint=f"{self.compiler_id}.artifact-fingerprint",
            execution_summary={"instruments": list(self.instrument_ids)},
            target_intent={
                "compiler_id": self.compiler_id,
                "batch_ordinal": str(request.batch_ordinal),
            },
            payload={
                "compiler_id": self.compiler_id,
                "batch_ordinal": str(request.batch_ordinal),
            },
        )
        return preparation.build(
            instrument_ids=self.instrument_ids,
            state_requirements=self.state_requirements,
            realtime_write_footprint=(
                self.realtime_write_footprint
                if self.realtime_write_batches is None
                or request.batch_ordinal in self.realtime_write_batches
                else ()
            ),
            realtime_state_invalidations=self.realtime_state_invalidations,
            next_batch_max_points=(
                self.batch_size
                if self.next_batch_capacities is None
                else self.next_batch_capacities[
                    min(
                        request.batch_ordinal,
                        len(self.next_batch_capacities) - 1,
                    )
                ]
            ),
            mapping=mapping,
            invocation=invocation,
            job_runtime=self.job_runtime,
            realize=_reject_realization,
        )


@dataclass
class _TrackingProvider:
    delegate: TestSignalInstrumentProvider = field(
        default_factory=TestSignalInstrumentProvider
    )
    describe_calls: int = 0
    connect_calls: int = 0

    @property
    def provider_id(self) -> str:
        return self.delegate.provider_id

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        self.describe_calls += 1
        return self.delegate.describe(context)

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> Never:
        del context
        self.connect_calls += 1
        raise AssertionError("planning must not connect an instrument")


def _reject_realization(
    _executed: DomainExecutionResult[dict[str, str]],
) -> Sequence[DomainResultValue[str]]:
    raise AssertionError("planning must not realize domain results")


def _bound_program(
    *,
    product_count: int = 1,
    domain_product_count: int | None = None,
    domain_call_count: int = 1,
    state_mode: Literal["none", "constant", "varying"] = "none",
    domain_before_state: bool = False,
    acquisition_before_domain: bool = False,
    record_instrument_products: bool = True,
    point_count: int = 2,
    point_grouping: PointGrouping | None = None,
    domain_input: ScalarExpr | None = None,
    parameter_overlays: Sequence[PointParameterOverlay] = (),
    parameter_data: ParameterRelationData | None = None,
    config: ConfigProfileSnapshot | None = None,
) -> BoundPlan:
    point_type = Table(
        columns=(
            TableColumn(
                "frequency",
                Scalar(QuantityType(unit="GHz")),
            ),
        ),
    )
    points = PointDomain(
        axes=(
            point_axis_values(
                "frequency",
                point_type.columns[0].value_type,
                tuple(
                    Quantity(
                        value=round(4.9 + 0.2 * index, 10),
                        unit="GHz",
                    )
                    for index in range(point_count)
                ),
            ),
        )
    )
    products = tuple(
        observable_product(f"signal-{index}", unit="ratio")
        for index in range(product_count)
    )
    selections = tuple(
        record_product(product, record_id=f"record-{index}")
        for index, product in enumerate(products)
    )
    selected_domain_product_count = (
        product_count if domain_product_count is None else domain_product_count
    )
    domain_executions: tuple[DomainExecutionFixture, ...] = ()
    if selected_domain_product_count:
        program_id = "program"
        selected = tuple(
            zip(
                products[:selected_domain_product_count],
                selections[:selected_domain_product_count],
                strict=True,
            )
        )
        result_bindings: list[DomainResultFixture] = []
        for index, (product, (use, _record)) in enumerate(selected):
            result_id = f"result-{index}"
            result_bindings.append(
                DomainResultFixture(
                    id=result_id,
                    product_id=product.id,
                    product_use_ids=(use.id,),
                )
            )
        if not 1 <= domain_call_count <= len(result_bindings):
            raise ValueError(
                "domain call count must cover at least one result per call"
            )
        execution_ids = (
            ("domain",)
            if domain_call_count == 1
            else tuple(f"domain-{index}" for index in range(domain_call_count))
        )
        result_groups = tuple(
            tuple(
                binding
                for index, binding in enumerate(result_bindings)
                if index % domain_call_count == call_index
            )
            for call_index in range(domain_call_count)
        )
        domain_executions = tuple(
            DomainExecutionFixture(
                id=execution_id,
                program=DomainProgramDef(
                    id=(
                        program_id
                        if domain_call_count == 1
                        else f"program-{call_index}"
                    ),
                    dialect_id="tests.domain",
                    dialect_version="1",
                    body=("test-program", call_index),
                    input_ports=(
                        (
                            DomainInputPort(
                                "drive_frequency",
                                domain_input.value_type,
                            ),
                        )
                        if domain_input is not None
                        else ()
                    ),
                    result_ports=tuple(
                        DomainResultPort(binding.id) for binding in bindings
                    ),
                ),
                inputs=(
                    {"drive_frequency": domain_input}
                    if domain_input is not None
                    else {}
                ),
                results=bindings,
            )
            for call_index, (execution_id, bindings) in enumerate(
                zip(execution_ids, result_groups, strict=True)
            )
        )
    instrument_acquisitions = tuple(
        instrument_acquisition(
            product,
            interface="test.scalar_signal/v1",
            result_id="signal",
        )
        for product in products[selected_domain_product_count:]
    )
    bindings = ExpressionTypeBindings(point_row=RowType.from_table(point_type))
    state_value = {
        "none": None,
        "constant": verified_scalar_expr(
            lit(Quantity(value=5.0, unit="GHz")),
            expected_type=Scalar(QuantityType(unit="GHz")),
        ),
        "varying": verified_scalar_expr(
            point_col("frequency", Scalar(QuantityType(unit="GHz"))),
            bindings=bindings,
            expected_type=Scalar(QuantityType(unit="GHz")),
        ),
    }[state_mode]
    state = (
        (
            state_property(
                logical_resource_port_id("source"),
                interface_id="test.set_frequency/v1",
                property_id="frequency",
                value=state_value,
            ),
        )
        if state_value is not None
        else ()
    )
    domain_and_state = (
        (*domain_executions, *state)
        if domain_before_state
        else (*state, *domain_executions)
    )
    effects = (
        (*instrument_acquisitions, *domain_and_state)
        if acquisition_before_domain
        else (*domain_and_state, *instrument_acquisitions)
    )
    recorded_selections = (
        selections
        if record_instrument_products
        else selections[:selected_domain_product_count]
    )
    program = program_fixture(
        point_domain=points,
        resource_requirements=(
            *(
                (
                    LogicalResourceRequirement(
                        port_id=logical_resource_port_id("source"),
                        capabilities=tuple(
                            InterfaceRef(interface_id)
                            for interface_id in (
                                ("test.set_frequency/v1", "test.scalar_signal/v1")
                                if state
                                else ("test.scalar_signal/v1",)
                            )
                        ),
                    ),
                )
                if state or instrument_acquisitions
                else ()
            ),
        ),
        parameter_overlays=tuple(parameter_overlays),
        effects=effects,
        point_grouping=point_grouping,
        product_defs=products,
        product_uses=tuple(use for use, _record in recorded_selections),
        record_uses=tuple(record for _use, record in recorded_selections),
    )
    environment = build_config_environment(load_config() if config is None else config)
    if parameter_data is not None:
        environment = replace(environment, parameters=parameter_data)
    return bind_program_facts(program, environment)


def _measurement_compute_identity(
    value: MeasurementValue,
) -> dict[str, MeasurementValue]:
    return {"result": value}


def _bound_instrument_fed_compute_program() -> BoundPlan:
    source = observable_product("source", unit="ratio")
    middle = observable_product("middle", unit="ratio")
    derived = observable_product("derived", unit="ratio")
    source_use = product_use(source.id)
    middle_use = product_use(middle.id)
    derived_use, derived_record = record_product(derived)
    computes = (
        BoundMeasurementCompute(
            id=MeasurementComputeId(SymbolId(local_id="normalize")),
            inputs=(
                BoundMeasurementComputeInput(
                    id="input",
                    product_id=source.id,
                    product_use_id=source_use.id,
                ),
            ),
            outputs=(
                BoundMeasurementComputeOutput(
                    id="result",
                    product_id=middle.id,
                    product_use_ids=(middle_use.id,),
                ),
            ),
            kernel=lambda values: _measurement_compute_identity(
                cast("MeasurementValue", values["input"])
            ),
        ),
        BoundMeasurementCompute(
            id=MeasurementComputeId(SymbolId(local_id="summarize")),
            inputs=(
                BoundMeasurementComputeInput(
                    id="input",
                    product_id=middle.id,
                    product_use_id=middle_use.id,
                ),
            ),
            outputs=(
                BoundMeasurementComputeOutput(
                    id="result",
                    product_id=derived.id,
                    product_use_ids=(derived_use.id,),
                ),
            ),
            kernel=lambda values: _measurement_compute_identity(
                cast("MeasurementValue", values["input"])
            ),
        ),
    )
    program = program_fixture(
        point_domain=PointDomain(axes=()),
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=logical_resource_port_id("source"),
                capabilities=(InterfaceRef("test.scalar_signal/v1"),),
            ),
        ),
        instrument_acquisitions=(
            instrument_acquisition(
                source,
                interface="test.scalar_signal/v1",
                result_id="signal",
            ),
        ),
        measurement_computes=computes,
        product_defs=(source, middle, derived),
        product_uses=(source_use, middle_use, derived_use),
        record_uses=(derived_record,),
    )
    return bind_program_facts(
        program,
        build_config_environment(load_config()),
    )


def _problem_codes(error: CheckFailed) -> set[str]:
    return {problem.code for problem in error.problems}


def _config_with_domain_resources(
    *instrument_ids: str,
) -> ConfigProfileSnapshot:
    config = load_config()
    selected = set(instrument_ids)
    known_ids = {instrument.id for instrument in config.instrument_registry.instruments}
    seed = config.instrument_registry.instruments[0]
    registry = config.instrument_registry.model_copy(
        update={
            "instruments": [
                *(
                    instrument.model_copy(
                        update={
                            "exclusivity_key": f"physical:{instrument.id}",
                        }
                    )
                    if instrument.id in selected
                    else instrument
                    for instrument in config.instrument_registry.instruments
                ),
                *(
                    seed.model_copy(
                        update={
                            "id": instrument_id,
                            "exclusivity_key": f"physical:{instrument_id}",
                        }
                    )
                    for instrument_id in instrument_ids
                    if instrument_id not in known_ids
                ),
            ],
        }
    )
    system = config.system.model_copy(
        update={
            "instrument_registry": registry,
            "domain_target": DomainTargetBinding(
                id="tests.domain.target",
                kind="tests.domain",
                instrument_ids=list(instrument_ids),
            ),
        }
    )
    return config.model_copy(update={"system": system})


def _assert_no_domain_effects(*compilers: _DomainCompiler) -> None:
    assert all(compiler.job_runtime.start_calls == 0 for compiler in compilers)


def _catalog(
    bound: BoundPlan,
    provider: InstrumentProvider | None = None,
) -> InstrumentContractCatalog:
    config = bound.environment.config
    if provider is None:
        return InstrumentContractCatalog(
            config_content_hash=config_content_hash(config),
        )
    return resolve_instrument_contract_catalog(
        config=config,
        provider_id=provider.provider_id,
        describe=provider.describe,
    )


def test_unified_planning_rejects_missing_local_catalog_before_effects() -> None:
    bound = _bound_program(state_mode="varying")
    compiler = _DomainCompiler("tests.missing-claim")

    with pytest.raises(CheckFailed) as captured:
        ExperimentSystem(
            instrument_catalog=_catalog(bound),
            domain_compiler=compiler,
        ).compile(bound)

    assert _problem_codes(captured.value) == {"local_instrument_catalog_missing"}
    assert captured.value.problems[0].details == {}
    assert all(
        problem.phase is ProblemPhase.PLANNING for problem in captured.value.problems
    )
    assert compiler.compile_calls == 0
    _assert_no_domain_effects(compiler)


def test_recorded_compute_runs_without_an_instrument_provider() -> None:
    @sc.experiment(id="test.recorded-compute", kind="compute")
    def definition(experiment: sc.ExperimentContext) -> None:
        score = experiment.compute(
            "score",
            fn=lambda: 2.5,
            output_type=sc.ScalarType(sc.FloatType()),
        )
        experiment.alias(score)

    bound = bind_program(
        compile_invocation(definition()).program,
        build_config_environment(load_config()),
    )
    plan = ExperimentSystem(instrument_catalog=_catalog(bound)).compile(bound)

    assert plan.host is None
    assert [node.local_id for node in bound.bindings.live_compute_ids] == ["score"]
    compute_operations = [
        operation.operation
        for operation in plan.coverage
        if isinstance(operation, RunCoverageEffect)
        and isinstance(operation.operation, ComputeOperation)
    ]
    assert len(compute_operations) == 1
    assert plan.measurements.runtime_value_ids == (
        bound.program.program.compute_nodes[0].result_id,
    )


def test_array_compute_results_are_ordered_and_recordable() -> None:
    observed: list[list[float]] = []

    def produce() -> np.ndarray:
        return np.asarray([1.0, 2.0, 3.0])

    def peak(*, trace: np.ndarray) -> float:
        observed.append(trace.tolist())
        return float(np.max(trace))

    @sc.experiment(id="test.recorded-array-compute", kind="compute")
    def definition(experiment: sc.ExperimentContext) -> None:
        trace = experiment.compute(
            "trace",
            fn=produce,
            output_type=sc.ArrayType(
                dtype="float64",
                dimensions=(sc.ArrayDimension("sample", 3),),
                unit="V",
            ),
        )
        maximum = experiment.compute(
            "peak",
            fn=peak,
            inputs={"trace": trace},
            output_type=sc.ScalarType(sc.FloatType()),
        )
        experiment.alias(trace)
        experiment.alias(maximum)

    bound = bind_program(
        compile_invocation(definition()).program,
        build_config_environment(load_config()),
    )
    plan = ExperimentSystem(instrument_catalog=_catalog(bound)).compile(bound)

    operations = [
        effect.operation
        for effect in plan.coverage
        if isinstance(effect, RunCoverageEffect)
        and isinstance(effect.operation, ComputeOperation)
    ]
    assert [operation.logical_compute_node_id for operation in operations] == [
        "trace",
        "peak",
    ]
    assert plan.measurements.runtime_value_ids == tuple(
        node.result_id for node in bound.program.program.compute_nodes
    )
    trace_record, peak_record = plan.measurements.records
    assert [axis.id for axis in trace_record.axes] == ["sample"]
    assert peak_record.axes == ()
    assert observed == []


def test_plan_stage_value_record_is_materialized_per_point() -> None:
    @sc.experiment(id="test.recorded-input", kind="compute")
    def definition(
        experiment: sc.ExperimentContext,
        threshold: Annotated[
            sc.Input[float],
            sc.ScalarType(sc.FloatType()),
        ] = 1.5,
    ) -> None:
        experiment.alias(sc.input_ref(threshold))

    bound = bind_program(
        compile_invocation(definition()).program,
        build_config_environment(load_config()),
    )
    plan = ExperimentSystem(instrument_catalog=_catalog(bound)).compile(bound)

    assert plan.host is None
    assert bound.bindings.live_compute_ids == frozenset()
    [candidate] = plan.measurements.static_value_candidates(plan.points.points[:1])
    assert candidate.value == 1.5
    [record] = plan.measurements.records
    assert record.id == "threshold"


def test_planning_executes_repeated_grid_in_snake_order() -> None:
    x = sc.coordinate("x", sc.ScalarType(sc.IntType()))
    y = sc.coordinate("y", sc.ScalarType(sc.IntType()))

    @sc.experiment(id="test.snake-repeat", kind="point_plan")
    def definition(experiment: sc.ExperimentContext) -> None:
        experiment.grid(
            sc.axis(x, (0, 1)),
            sc.axis(y, (0, 1, 2)),
            repeat=2,
            traversal="snake",
        )

    bound = bind_program(
        compile_invocation(definition()).program,
        build_config_environment(load_config()),
    )
    plan = ExperimentSystem(instrument_catalog=_catalog(bound)).compile(bound)

    assert tuple(point.ordinal for point in plan.points.points) == tuple(range(12))
    assert tuple(
        point_index
        for operation in plan.coverage
        if isinstance(operation, RunCoverageCheckpoint)
        for point_index in operation.point_indices
    ) == (0, 1, 2, 3, 4, 5, 10, 11, 8, 9, 6, 7)


def test_planning_and_preview_resolve_grouped_snake_as_one_schedule() -> None:
    x = sc.coordinate("x", sc.ScalarType(sc.IntType()))
    y = sc.coordinate("y", sc.ScalarType(sc.IntType()))

    @sc.experiment(id="test.grouped-snake", kind="point_plan")
    def definition(experiment: sc.ExperimentContext) -> None:
        experiment.grid(
            sc.axis(x, (0, 1)),
            sc.axis(y, (0, 1, 2)),
            traversal="snake",
        )
        experiment.group_points("y-within-x", varying=(y,))

    bound = bind_program(
        compile_invocation(definition()).program,
        build_config_environment(load_config()),
    )
    plan = ExperimentSystem(instrument_catalog=_catalog(bound)).compile(bound)
    preview = build_run_program_preview(plan)

    assert [group.ordinals for group in plan.point_groups] == [
        (0, 1, 2),
        (5, 4, 3),
    ]
    assert preview.point_schedule is not None
    assert preview.point_schedule.traversal == "snake"
    grouping = preview.point_schedule.grouping
    assert grouping is not None
    assert grouping.id == "y-within-x"
    assert [group.point_indices for group in grouping.groups] == [
        (0, 1, 2),
        (5, 4, 3),
    ]


def test_planning_rejects_catalog_for_another_config() -> None:
    bound = _bound_program()
    catalog = _catalog(bound).model_copy(
        update={"config_content_hash": "sha256:" + ("f" * 64)}
    )

    with pytest.raises(ProviderContractError) as captured:
        ExperimentSystem(
            instrument_catalog=catalog,
            domain_compiler=_DomainCompiler("tests.catalog-config"),
        ).compile(bound)

    [issue] = captured.value.problems
    assert issue.code == "instrument_catalog_config_mismatch"
    assert issue.phase is ProblemPhase.PROVIDER_PREFLIGHT
    assert issue.details == {
        "expected": config_content_hash(bound.environment.config),
        "actual": catalog.config_content_hash,
    }


def test_domain_only_planning_ignores_unrelated_catalog_problems() -> None:
    bound = _bound_program()
    catalog = _catalog(bound).model_copy(
        update={
            "problems": (
                problem(
                    "tests.instrument_unavailable",
                    "an unused instrument is unavailable",
                    phase=ProblemPhase.PROVIDER_PREFLIGHT,
                ),
            )
        }
    )

    plan = ExperimentSystem(
        instrument_catalog=catalog,
        domain_compiler=_DomainCompiler("tests.catalog-problems"),
    ).compile(bound)

    assert plan.host is None


def test_local_planning_rejects_catalog_problems() -> None:
    bound = _bound_program(state_mode="constant")
    catalog = _catalog(bound, TestSignalInstrumentProvider()).model_copy(
        update={
            "problems": (
                problem(
                    "tests.instrument_unavailable",
                    "a required instrument is unavailable",
                    phase=ProblemPhase.PROVIDER_PREFLIGHT,
                ),
            )
        }
    )

    with pytest.raises(ProviderContractError) as captured:
        ExperimentSystem(
            instrument_catalog=catalog,
            domain_compiler=_DomainCompiler("tests.catalog-problems"),
        ).compile(bound)

    assert {issue.code for issue in captured.value.problems} == {
        "tests.instrument_unavailable"
    }


def test_system_builder_receives_daemon_catalog() -> None:
    config = load_config()
    provider = TestSignalInstrumentProvider()
    catalog = resolve_instrument_contract_catalog(
        config=config,
        provider_id=provider.provider_id,
        describe=provider.describe,
    )
    calls: list[tuple[ConfigProfileSnapshot, InstrumentContractCatalog]] = []

    def build(
        selected: ConfigProfileSnapshot,
        contracts: InstrumentContractCatalog,
    ) -> ExperimentSystem:
        calls.append((selected, contracts))
        return ExperimentSystem(instrument_catalog=contracts)

    built = build_experiment_system(build, config, catalog)
    catalog_only = build_experiment_system(None, config, catalog)

    assert calls == [(config, catalog)]
    assert built.instrument_catalog is catalog
    assert catalog_only.instrument_catalog is catalog


def test_system_builder_cannot_replace_daemon_catalog() -> None:
    config = load_config()
    provider = TestSignalInstrumentProvider()
    catalog = resolve_instrument_contract_catalog(
        config=config,
        provider_id=provider.provider_id,
        describe=provider.describe,
    )
    replacement = InstrumentContractCatalog(
        config_content_hash=catalog.config_content_hash,
    )

    with pytest.raises(ValueError, match="must retain"):
        build_experiment_system(
            lambda _config, _catalog: ExperimentSystem(
                instrument_catalog=replacement,
            ),
            config,
            catalog,
        )


def test_planning_keeps_compute_outputs_out_of_local_acquisition() -> None:
    bound = _bound_instrument_fed_compute_program()

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, TestSignalInstrumentProvider())
    ).compile(bound)

    first, second = plan.measurement_computes
    assert [first.id.qualified_name, second.id.qualified_name] == [
        "normalize",
        "summarize",
    ]
    assert first.inputs[0].product_id.qualified_name == "source"
    assert second.inputs[0].product_id.qualified_name == "middle"
    [collect] = [
        effect.operation
        for effect in plan.coverage
        if isinstance(effect, RunCoverageEffect)
        and isinstance(effect.operation, CollectOperation)
    ]
    assert collect.result_bindings[0].product_use_ids == (
        first.inputs[0].product_use_id,
    )
    preview = build_run_program_preview(plan)
    assert preview.observation_compute_ids == ("normalize", "summarize")
    normalize, summarize = preview.computes
    assert normalize.placement == "observation"
    assert normalize.implementation == "python:normalize"
    assert not normalize.deterministic
    assert normalize.inputs == ("input",)
    assert normalize.demanded_by == ("compute:summarize",)
    assert summarize.demanded_by == ("record:derived",)
    assert [(product.id, product.shape) for product in preview.transient_products] == [
        ("source", ()),
        ("middle", ()),
    ]
    assert [record.id for record in preview.records] == ["derived"]


def test_domain_target_partitions_complete_point_space_by_capacity() -> None:
    bound = _bound_program(point_count=2)
    compiler = _DomainCompiler(
        "tests.target-capacity",
        batch_size=1,
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)

    assert len(plan.points.points) == 2
    assert tuple(point.ordinal for point in plan.points.points) == (0, 1)
    assert compiler.compile_calls == 0
    assert [
        operation.point_ordinals
        for operation in plan.coverage
        if isinstance(operation, RunDomainJob)
    ] == [(0,), (1,)]
    assert compiler.compile_calls == 2
    assert compiler.initial_batch_requests == [2]
    assert [request.point_ordinals for request in compiler.compile_requests] == [
        (0,),
        (1,),
    ]
    assert not any(
        request.inspection_requested for request in compiler.compile_requests
    )


def test_free_preview_compiles_a_canonical_unplanned_point() -> None:
    frequency_type = Scalar(QuantityType(unit="GHz"))
    point_type = Table(columns=(TableColumn("frequency", frequency_type),))
    domain_input = verified_scalar_expr(
        point_col("frequency", frequency_type),
        bindings=ExpressionTypeBindings(point_row=RowType.from_table(point_type)),
        expected_type=frequency_type,
    )
    bound = _bound_program(domain_input=domain_input)
    compiler = _DomainCompiler("tests.free-preview")
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)

    preview = build_run_program_preview(
        plan,
        coordinates={"frequency": Quantity(5050.0, "MHz")},
        coordinate_mode="free",
    )

    assert preview.selected_point is not None
    assert preview.selected_point.point_index is None
    assert not preview.selected_point.is_planned
    assert preview.selected_point.source == "operator"
    assert preview.selected_point.coordinates == {"frequency": Quantity(5.05, "GHz")}
    assert preview.selected_point.proposal_fingerprint is not None
    assert preview.domain_inspections == ()
    assert compiler.prepared_inputs == [(Quantity(5.05, "GHz"),)]
    [request] = compiler.compile_requests
    assert request.point_ordinals == (0,)
    assert request.inspection_requested
    assert cast(
        "LogicalPointId", request.points[0].native
    ).domain_id.domain_id.startswith("root.inspection-")


def test_snap_preview_selects_nearest_planned_point_without_free_compilation() -> None:
    bound = _bound_program(point_count=3)
    compiler = _DomainCompiler("tests.snap-preview")
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)
    compile_calls = compiler.compile_calls

    preview = build_run_program_preview(
        plan,
        coordinates={"frequency": Quantity(5.04, "GHz")},
        coordinate_mode="snap",
    )

    assert preview.selected_point is not None
    assert preview.selected_point.point_index == 1
    assert preview.selected_point.is_planned
    assert preview.selected_point.coordinates == {"frequency": Quantity(5.1, "GHz")}
    assert compiler.compile_calls == compile_calls + 1
    assert compiler.prepared_inputs == []
    [request] = compiler.compile_requests
    assert request.point_ordinals == (1,)
    assert request.inspection_requested


def test_domain_target_initial_batch_must_fit_the_complete_point_space() -> None:
    bound = _bound_program(point_count=2)
    compiler = _DomainCompiler(
        "tests.invalid-partition",
        initial_size=3,
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)
    with pytest.raises(ValueError, match="positive covered point count"):
        tuple(plan.coverage)


def test_domain_target_candidate_must_fit_its_retained_byte_budget() -> None:
    bound = _bound_program(point_count=2)
    compiler = _DomainCompiler(
        "tests.invalid-retained-cost",
        max_retained_bytes=1024,
        retained_bytes=1025,
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)

    with pytest.raises(ValueError, match="retained-byte limit"):
        tuple(plan.coverage)


def test_domain_target_cost_must_cover_its_compatible_prefix() -> None:
    bound = _bound_program(point_count=2)
    compiler = _DomainCompiler(
        "tests.invalid-analyzed-cost",
        analyzed_point_count=1,
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)

    with pytest.raises(ValueError, match="must cover its compatible prefix"):
        tuple(plan.coverage)


def test_domain_target_adapts_followup_batches_from_compiler_feedback() -> None:
    bound = _bound_program(point_count=10)
    compiler = _DomainCompiler(
        "tests.adaptive-capacity",
        initial_size=1,
        next_batch_capacities=(3,),
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)

    jobs = tuple(
        operation for operation in plan.coverage if isinstance(operation, RunDomainJob)
    )
    assert tuple(job.point_ordinals for job in jobs) == (
        (0,),
        (1, 2, 3),
        (4, 5, 6),
        (7, 8, 9),
    )


def test_domain_batches_may_split_a_recovery_group() -> None:
    bound = _bound_program(
        point_count=6,
        point_grouping=PointGrouping("comparison", ("frequency",)),
    )
    compiler = _DomainCompiler(
        "tests.recovery-groups",
        initial_size=3,
        next_batch_capacities=(3,),
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)
    operations = tuple(plan.coverage)

    assert [request.point_ordinals for request in compiler.compatibility_requests] == [
        (0, 1, 2),
        (3, 4, 5),
    ]
    compatibility_cuts = [
        request.legal_cut_offsets for request in compiler.compatibility_requests
    ]
    assert compatibility_cuts == [
        (1, 2, 3),
        (1, 2, 3),
    ]
    assert [request.point_ordinals for request in compiler.compile_requests] == [
        (0, 1, 2),
        (3, 4, 5),
    ]
    assert [
        operation.point_indices
        for operation in operations
        if isinstance(operation, RunCoverageCheckpoint)
    ] == [(0, 1, 2, 3, 4, 5)]
    preview_bound = _bound_program(
        product_count=0,
        domain_product_count=0,
        point_count=6,
        point_grouping=PointGrouping("comparison", ("frequency",)),
    )
    preview = build_run_program_preview(
        ExperimentSystem(instrument_catalog=_catalog(preview_bound)).compile(
            preview_bound
        )
    )
    assert preview.point_schedule is not None
    assert preview.point_schedule.traversal == "forward"
    grouping = preview.point_schedule.grouping
    assert grouping is not None
    assert grouping.id == "comparison"
    assert grouping.varying_coordinate_ids == ("frequency",)
    assert grouping.group_count == 1
    assert grouping.groups[0].key == {}
    assert grouping.groups[0].point_indices == (0, 1, 2, 3, 4, 5)


def test_domain_compiler_may_choose_a_prefix_inside_a_recovery_group() -> None:
    bound = _bound_program(
        point_count=4,
        point_grouping=PointGrouping("comparison", ("frequency",)),
    )
    compiler = _DomainCompiler(
        "tests.recovery-group-prefix",
        initial_size=4,
        compatible_sizes=(1,),
    )
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)

    operations = tuple(plan.coverage)

    assert compiler.compatibility_requests[0].legal_cut_offsets == (1, 2, 3, 4)
    assert [
        operation.point_indices
        for operation in operations
        if isinstance(operation, RunCoverageCheckpoint)
    ] == [(0, 1, 2, 3)]


def test_host_state_boundary_may_split_a_recovery_group() -> None:
    bound = _bound_program(
        state_mode="varying",
        point_count=2,
        point_grouping=PointGrouping("comparison", ("frequency",)),
    )
    compiler = _DomainCompiler("tests.host-state-splits-recovery-group")
    provider = _TrackingProvider()
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, provider),
        domain_compiler=compiler,
    ).compile(bound)

    operations = tuple(plan.coverage)

    assert [
        operation.point_indices
        for operation in operations
        if isinstance(operation, RunCoverageCheckpoint)
    ] == [(0, 1)]


def test_static_coverage_resume_starts_only_after_a_complete_group() -> None:
    bound = _bound_program(
        product_count=0,
        domain_product_count=0,
        point_count=4,
        point_grouping=PointGrouping("comparison", ("frequency",)),
    )
    plan = ExperimentSystem(instrument_catalog=_catalog(bound)).compile(bound)

    with pytest.raises(ValueError, match="inside a point group"):
        tuple(plan.coverage.suffix(1))
    with pytest.raises(ValueError, match="inside a point group"):
        tuple(plan.coverage.suffix(2))
    assert tuple(plan.coverage.suffix(4)) == ()


def test_domain_target_uses_the_largest_compatible_candidate_prefix() -> None:
    bound = _bound_program(point_count=10)
    compiler = _DomainCompiler(
        "tests.compatible-prefix",
        batch_size=5,
        initial_size=5,
        compatible_sizes=(2, 3, 5),
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)

    jobs = tuple(
        operation for operation in plan.coverage if isinstance(operation, RunDomainJob)
    )
    assert [request.point_ordinals for request in compiler.compatibility_requests] == [
        (0, 1, 2, 3, 4),
        (2, 3, 4, 5, 6),
        (5, 6, 7, 8, 9),
    ]
    assert [request.point_ordinals for request in compiler.compile_requests] == [
        (0, 1),
        (2, 3, 4),
        (5, 6, 7, 8, 9),
    ]
    assert [job.point_ordinals for job in jobs] == [
        (0, 1),
        (2, 3, 4),
        (5, 6, 7, 8, 9),
    ]


@pytest.mark.parametrize("compatible_size", (0, 3))
def test_domain_target_rejects_an_invalid_compatible_prefix(
    compatible_size: int,
) -> None:
    bound = _bound_program(point_count=2)
    compiler = _DomainCompiler(
        "tests.invalid-compatible-prefix",
        initial_size=2,
        compatible_sizes=(compatible_size,),
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)
    with pytest.raises(ValueError, match="positive candidate prefix ending"):
        tuple(plan.coverage)


def test_local_effect_materialization_reuses_the_bounded_initial_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialized_ordinals: list[tuple[int, ...]] = []

    def record_materialization(
        bound_points: MaterializedBoundPoints,
        *,
        target: LocalTargetPlan,
        point_ordinals: tuple[int, ...],
    ) -> MaterializedLocalEffects:
        materialized_ordinals.append(point_ordinals)
        return materialize_local_execution(
            bound_points,
            target=target,
            point_ordinals=point_ordinals,
        )

    monkeypatch.setattr(
        "scopecat.planning.system.materialize_local_execution",
        record_materialization,
    )
    bound = _bound_program(domain_product_count=0, point_count=300)

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, _TrackingProvider())
    ).compile(bound)

    assert materialized_ordinals == [(0,)]
    tuple(plan.coverage)
    assert all(len(batch) <= 256 for batch in materialized_ordinals)
    assert tuple(
        ordinal for batch in materialized_ordinals for ordinal in batch
    ) == tuple(range(300))


def test_point_invariant_state_reuses_only_the_initial_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialized_ordinals: list[tuple[int, ...]] = []

    def record_materialization(
        bound_points: MaterializedBoundPoints,
        *,
        target: LocalTargetPlan,
        point_ordinals: tuple[int, ...],
    ) -> MaterializedLocalEffects:
        materialized_ordinals.append(point_ordinals)
        return materialize_local_execution(
            bound_points,
            target=target,
            point_ordinals=point_ordinals,
        )

    monkeypatch.setattr(
        "scopecat.planning.system.materialize_local_execution",
        record_materialization,
    )
    bound = _bound_program(state_mode="constant", point_count=300)
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, _TrackingProvider()),
        domain_compiler=_DomainCompiler("tests.invariant-state"),
    ).compile(bound)

    assert materialized_ordinals == [(0,)]
    coverage = tuple(plan.coverage)

    assert materialized_ordinals == [(0,)]
    assert [
        operation.point_index
        for operation in coverage
        if isinstance(operation, RunCoverageEffect)
    ] == [0]


def test_large_plan_preview_samples_edges_without_hiding_total_point_count() -> None:
    from scopecat.planning.preflight import ExactQuantity, summarize_preflight

    bound = _bound_program(point_count=10_000, state_mode="varying")
    compiler = _DomainCompiler("tests.bounded-preflight", batch_size=32)
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, TestSignalInstrumentProvider()),
        domain_compiler=compiler,
    ).compile(bound)
    assert compiler.compile_calls == 0
    preview = build_run_program_preview(plan)
    stage = summarize_preflight(
        preview,
        stage_id="source",
        label="Large source scan",
        config_content_hash=plan.config_content_hash,
        configuration="accepted",
        configuration_meaning="Read-only source configuration",
        executions=ExactQuantity(value=1, unit="runs", basis="Declared test workflow"),
    )
    assert compiler.compile_calls == 1
    assert compiler.compile_requests[0].point_ordinals == (0,)
    assert stage.points_per_execution == ExactQuantity(
        value=10_000, unit="points", basis="Static point-plan cardinality"
    )
    assert stage.planned_settings == preview.planned_settings
    [setting] = stage.planned_settings
    assert setting.point_index == 0
    assert setting.setting.target.property_id == "frequency"
    assert setting.setting.value.root == Quantity(4.9, "GHz")
    assert stage.planned_setting_limit == 64
    assert not stage.planned_settings_truncated
    assert stage.sampled_points == stage.sampled_point_limit == 64
    assert stage.selected_points == stage.selected_point_limit == 1
    assert preview.points_truncated
    assert tuple(point.point_index for point in preview.points) == (
        *range(32),
        *range(9968, 10_000),
    )
    assert stage.shots_per_point_per_entity.kind == "unknown"
    assert all(cost.quantity.kind == "unknown" for cost in stage.costs)


def test_run_requirements_and_host_order_include_only_used_local_instruments() -> None:
    config = load_config()
    seed_instrument = config.instrument_registry.instruments[0].model_copy(
        update={"exclusivity_key": "physical:source-0"}
    )
    config = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "instrument_registry": config.instrument_registry.model_copy(
                        update={
                            "instruments": [
                                seed_instrument,
                                seed_instrument.model_copy(
                                    update={
                                        "id": "unused-0",
                                        "exclusivity_key": "unused-0",
                                    }
                                ),
                            ]
                        }
                    )
                }
            )
        }
    )
    provider = _TrackingProvider()
    bound = _bound_program(domain_product_count=0, config=config)

    plan = ExperimentSystem(instrument_catalog=_catalog(bound, provider)).compile(bound)

    assert plan.resource_requirements == (ResourceRequirement("source-0"),)
    assert plan.host is not None
    assert plan.host.resource_order == ("source-0",)
    assert set(plan.host.advertised_descriptions) == {"source-0", "unused-0"}


def test_domain_target_footprint_contains_only_compiled_instruments() -> None:
    bound = _bound_program(
        config=_config_with_domain_resources("source-0", "target-member-1")
    )
    provider = _TrackingProvider()

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, provider),
        domain_compiler=_DomainCompiler(
            "tests.complete-target-footprint",
            instrument_ids=("source-0",),
        ),
    ).compile(bound)

    assert plan.domain_target_requirement == DomainTargetRequirement(
        id="tests.domain.target",
        kind="tests.domain",
        instrument_ids=("source-0",),
    )
    assert plan.resource_requirements == (ResourceRequirement("source-0"),)
    assert plan.host is not None
    assert plan.host.resource_order == ("source-0",)


def test_domain_compiler_cannot_exceed_configured_target_authority() -> None:
    bound = _bound_program(config=_config_with_domain_resources("source-0"))

    with pytest.raises(CheckFailed) as captured:
        ExperimentSystem(
            instrument_catalog=_catalog(bound, _TrackingProvider()),
            domain_compiler=_DomainCompiler(
                "tests.target-authority",
                instrument_ids=("outside-target",),
            ),
        ).compile(bound)

    assert _problem_codes(captured.value) == {"domain_target_instrument_unauthorized"}
    assert captured.value.problems[0].details == {"instrument_ids": ("outside-target",)}


def test_parameter_overlay_binding_is_shared_with_domain_inputs() -> None:
    frequency_type = Scalar(QuantityType(unit="GHz"))
    point_type = Table(
        columns=(TableColumn("frequency", frequency_type),),
    )
    bindings = ExpressionTypeBindings(
        point_row=RowType.from_table(point_type),
    )
    domain_input = verified_scalar_expr(
        parameter_lookup(
            READOUT_FREQUENCY_LOOKUP,
            key={"device_id": "r0"},
        ),
        bindings=bindings,
        expected_type=frequency_type,
    )
    overlay = overlay_parameter_cell(
        "readout_devices",
        row_index=0,
        key={"device_id": "r0"},
        column_id="frequency",
        axis_id="frequency",
        value_type=frequency_type,
    )
    bound = _bound_program(
        domain_input=domain_input,
        parameter_overlays=(overlay,),
        parameter_data=parameter_fixture_data(),
    )
    compiler = _DomainCompiler("tests.parameter-binding")

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)

    assert plan.host is None
    assert tuple(point.ordinal for point in plan.points.points) == (0, 1)
    [domain_job] = (
        operation for operation in plan.coverage if isinstance(operation, RunDomainJob)
    )
    assert isinstance(domain_job, RunDomainJob)
    assert isinstance(domain_job.execution, PreparedDomainExecution)
    assert compiler.prepared_inputs == [
        (
            Quantity(value=4.9, unit="GHz"),
            Quantity(value=5.1, unit="GHz"),
        )
    ]


def test_host_state_bounds_domain_compilation_regions() -> None:
    bound = _bound_program(state_mode="varying")
    compiler = _DomainCompiler("tests.effect-regions")
    provider = _TrackingProvider()
    system = ExperimentSystem(
        instrument_catalog=_catalog(bound, provider),
        domain_compiler=compiler,
    )

    plan = system.compile(bound)
    coverage = tuple(plan.coverage)

    assert tuple(point.ordinal for point in plan.points.points) == (0, 1)
    assert [
        operation.point_ordinals
        for operation in coverage
        if isinstance(operation, RunDomainJob)
    ] == [(0,), (1,)]
    assert compiler.initial_batch_requests == [2]
    assert [
        operation.point_index
        for operation in coverage
        if isinstance(operation, RunCoverageEffect)
    ] == [0, 1]
    assert [
        point_index
        for operation in coverage
        if isinstance(operation, RunCoverageCheckpoint)
        for point_index in operation.point_indices
    ] == [0, 1]
    assert provider.describe_calls == 1
    assert provider.connect_calls == 0
    assert compiler.compile_calls == 2
    _assert_no_domain_effects(compiler)


def test_domain_and_local_state_retain_declared_order_in_each_batch() -> None:
    bound = _bound_program(
        state_mode="constant",
        domain_before_state=True,
    )
    provider = _TrackingProvider()
    compiler = _DomainCompiler("tests.declared-effect-order", batch_size=1)
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, provider),
        domain_compiler=compiler,
    ).compile(bound)

    consequential = tuple(
        operation
        for operation in plan.coverage
        if not isinstance(operation, RunCoverageCheckpoint)
    )

    assert [type(operation) for operation in consequential] == [
        RunDomainJob,
        RunCoverageEffect,
        RunDomainJob,
    ]
    assert isinstance(consequential[1], RunCoverageEffect)
    assert isinstance(consequential[1].operation, ApplyStateOperation)
    assert compiler.initial_batch_requests == [2]


def test_stable_host_state_prepares_a_domain_segment_once() -> None:
    bound = _bound_program(state_mode="constant")
    provider = _TrackingProvider()
    compiler = _DomainCompiler("tests.state-before-domain", batch_size=1)

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, provider),
        domain_compiler=compiler,
    ).compile(bound)
    coverage = tuple(plan.coverage)

    consequential = tuple(
        operation
        for operation in coverage
        if not isinstance(operation, RunCoverageCheckpoint)
    )
    assert [type(operation) for operation in consequential] == [
        RunCoverageEffect,
        RunDomainJob,
        RunDomainJob,
    ]
    assert isinstance(consequential[0], RunCoverageEffect)
    assert isinstance(consequential[0].operation, ApplyStateOperation)


def test_local_acquisition_and_domain_job_may_share_one_instrument() -> None:
    config = _config_with_domain_resources("source-0")
    bound = _bound_program(
        product_count=2,
        domain_product_count=1,
        config=config,
    )
    compiler = _DomainCompiler(
        "tests.instrument-overlap",
        instrument_ids=("source-0",),
    )

    provider = _TrackingProvider()
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, provider),
        domain_compiler=compiler,
    ).compile(bound)
    tuple(plan.coverage)

    assert plan.resource_requirements == (ResourceRequirement("source-0"),)
    assert plan.domain_target_requirement == DomainTargetRequirement(
        id="tests.domain.target",
        kind="tests.domain",
        instrument_ids=("source-0",),
    )
    assert compiler.compile_calls == 2


def test_stable_host_state_and_domain_job_may_share_one_instrument() -> None:
    config = _config_with_domain_resources("source-0")
    bound = _bound_program(state_mode="constant", config=config)
    compiler = _DomainCompiler(
        "tests.disjoint-state",
        instrument_ids=("source-0",),
        state_requirements=(
            DomainStateRequirement(
                address=DomainStateAddress(
                    instrument_id="source-0",
                    interface_id="test.set_frequency/v1",
                    property_id="frequency",
                ),
                value=StateValue(Quantity(value=5000.0, unit="MHz")),
            ),
        ),
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, _TrackingProvider()),
        domain_compiler=compiler,
    ).compile(bound)
    coverage = tuple(plan.coverage)

    assert [
        operation.point_index
        for operation in coverage
        if isinstance(operation, RunCoverageEffect)
    ] == [0]
    assert [
        operation.point_ordinals
        for operation in coverage
        if isinstance(operation, RunDomainJob)
    ] == [(0, 1)]
    assert plan.resource_requirements == (ResourceRequirement("source-0"),)


def test_domain_state_requirement_must_follow_its_host_preparation() -> None:
    config = _config_with_domain_resources("source-0")
    bound = _bound_program(
        state_mode="constant",
        domain_before_state=True,
        config=config,
    )
    compiler = _DomainCompiler(
        "tests.state-requirement-order",
        instrument_ids=("source-0",),
        state_requirements=(
            DomainStateRequirement(
                address=DomainStateAddress(
                    instrument_id="source-0",
                    interface_id="test.set_frequency/v1",
                    property_id="frequency",
                ),
                value=StateValue(Quantity(value=5.0, unit="GHz")),
            ),
        ),
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, _TrackingProvider()),
        domain_compiler=compiler,
    ).compile(bound)
    with pytest.raises(CheckFailed) as captured:
        tuple(plan.coverage)

    assert _problem_codes(captured.value) == {"domain_state_requirement_missing"}
    assert captured.value.problems[0].details == {
        "domain_job_id": "domain:batch-0",
        "state_address": "source-0:test.set_frequency/v1.frequency",
        "point_ordinals": (0, 1),
    }


def test_domain_state_requirement_must_match_the_host_value() -> None:
    config = _config_with_domain_resources("source-0")
    bound = _bound_program(state_mode="constant", config=config)
    compiler = _DomainCompiler(
        "tests.state-requirement-value",
        instrument_ids=("source-0",),
        state_requirements=(
            DomainStateRequirement(
                address=DomainStateAddress(
                    instrument_id="source-0",
                    interface_id="test.set_frequency/v1",
                    property_id="frequency",
                ),
                value=StateValue(Quantity(value=5.1, unit="GHz")),
            ),
        ),
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, _TrackingProvider()),
        domain_compiler=compiler,
    ).compile(bound)
    with pytest.raises(CheckFailed) as captured:
        tuple(plan.coverage)

    assert _problem_codes(captured.value) == {"domain_state_requirement_mismatch"}
    assert captured.value.problems[0].details["guaranteed_by"] == {
        "kind": "host_state",
        "point_index": 0,
    }


def test_runtime_readback_does_not_create_a_planning_state_guarantee() -> None:
    config = _config_with_domain_resources("source-0")
    bound = _bound_program(
        product_count=2,
        domain_product_count=1,
        acquisition_before_domain=True,
        config=config,
    )
    compiler = _DomainCompiler(
        "tests.readback-is-not-state-guarantee",
        instrument_ids=("source-0",),
        state_requirements=(
            DomainStateRequirement(
                address=DomainStateAddress(
                    instrument_id="source-0",
                    interface_id="test.set_frequency/v1",
                    property_id="frequency",
                ),
                value=StateValue(Quantity(value=5.0, unit="GHz")),
            ),
        ),
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, _TrackingProvider()),
        domain_compiler=compiler,
    ).compile(bound)
    with pytest.raises(CheckFailed) as captured:
        tuple(plan.coverage)

    assert _problem_codes(captured.value) == {"domain_state_requirement_missing"}


def test_domain_state_invalidation_requires_repreparation_before_next_job() -> None:
    config = _config_with_domain_resources("source-0")
    bound = _bound_program(state_mode="constant", config=config)
    address = DomainStateAddress(
        instrument_id="source-0",
        interface_id="test.set_frequency/v1",
        property_id="frequency",
    )
    compiler = _DomainCompiler(
        "tests.state-invalidation",
        state_requirements=(
            DomainStateRequirement(
                address=address,
                value=StateValue(Quantity(value=5.0, unit="GHz")),
            ),
        ),
        realtime_state_invalidations=(address,),
        batch_size=1,
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, _TrackingProvider()),
        domain_compiler=compiler,
    ).compile(bound)
    with pytest.raises(CheckFailed) as captured:
        tuple(plan.coverage)

    assert _problem_codes(captured.value) == {"domain_state_requirement_missing"}
    assert captured.value.problems[0].details == {
        "domain_job_id": "domain:batch-1",
        "state_address": "source-0:test.set_frequency/v1.frequency",
        "point_ordinals": (1,),
        "invalidated_by": {
            "kind": "domain_realtime_invalidation",
            "domain_job_id": "domain:batch-0",
            "point_ordinals": (0,),
        },
    }


def test_planning_rejects_host_and_domain_writes_to_the_same_property() -> None:
    config = _config_with_domain_resources("source-0")
    bound = _bound_program(state_mode="constant", config=config)
    compiler = _DomainCompiler(
        "tests.state-write-conflict",
        instrument_ids=("source-0",),
        realtime_write_footprint=(
            DomainStateAddress(
                instrument_id="source-0",
                interface_id="test.set_frequency/v1",
                property_id="frequency",
            ),
        ),
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, _TrackingProvider()),
        domain_compiler=compiler,
    ).compile(bound)
    with pytest.raises(CheckFailed) as captured:
        tuple(plan.coverage)

    assert _problem_codes(captured.value) == {"host_domain_state_write_conflict"}
    assert captured.value.problems[0].details == {
        "state_addresses": ("source-0:test.set_frequency/v1.frequency",)
    }


def test_planning_detects_a_domain_write_after_static_host_state_is_coalesced() -> None:
    config = _config_with_domain_resources("source-0")
    bound = _bound_program(state_mode="constant", config=config)
    compiler = _DomainCompiler(
        "tests.later-state-write-conflict",
        instrument_ids=("source-0",),
        realtime_write_footprint=(
            DomainStateAddress(
                instrument_id="source-0",
                interface_id="test.set_frequency/v1",
                property_id="frequency",
            ),
        ),
        realtime_write_batches=frozenset({1}),
        batch_size=1,
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, _TrackingProvider()),
        domain_compiler=compiler,
    ).compile(bound)
    with pytest.raises(CheckFailed) as captured:
        tuple(plan.coverage)

    assert _problem_codes(captured.value) == {"host_domain_state_write_conflict"}


def test_unused_local_acquisition_does_not_fragment_domain_coverage() -> None:
    bound = _bound_program(
        product_count=2,
        domain_product_count=1,
        record_instrument_products=False,
    )
    compiler = _DomainCompiler("tests.unused-acquisition")
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)

    [domain] = (
        operation for operation in plan.coverage if isinstance(operation, RunDomainJob)
    )

    assert tuple(point.ordinal for point in plan.points.points) == (0, 1)
    assert domain.point_ordinals == (0, 1)
    assert [request.point_ordinals for request in compiler.compile_requests] == [(0, 1)]


def test_domain_compiler_coalesces_a_stable_host_state_region() -> None:
    bound = _bound_program(state_mode="constant")
    compiler = _DomainCompiler("tests.constant-peripheral")
    provider = _TrackingProvider()
    system = ExperimentSystem(
        instrument_catalog=_catalog(bound, provider),
        domain_compiler=compiler,
    )

    plan = system.compile(bound)
    coverage = tuple(plan.coverage)
    local_effects = plan.host
    assert local_effects is not None
    point_catalog = plan.points
    assert point_catalog.experiment_id == bound.program.experiment_id
    assert point_catalog.experiment_kind == bound.program.kind
    assert point_catalog.coordinate_ids == ("frequency",)
    assert tuple(point.ordinal for point in point_catalog.points) == (0, 1)
    assert [point.coordinates for point in point_catalog.points] == [
        {"frequency": Quantity(value=4.9, unit="GHz")},
        {"frequency": Quantity(value=5.1, unit="GHz")},
    ]
    domain_jobs = tuple(
        operation for operation in coverage if isinstance(operation, RunDomainJob)
    )
    assert tuple(job.point_ordinals for job in domain_jobs) == ((0, 1),)
    assert all(
        isinstance(domain_job.execution, PreparedDomainExecution)
        for domain_job in domain_jobs
    )
    assert provider.describe_calls == 1
    assert provider.connect_calls == 0
    assert compiler.compile_calls == 1
    assert compiler.initial_batch_requests == [2]
    assert [job.id for job in domain_jobs] == ["domain:batch-0"]
    assert [
        operation.point_index
        for operation in coverage
        if isinstance(operation, RunCoverageEffect)
    ] == [0]
    _assert_no_domain_effects(compiler)


def test_ordered_domain_calls_share_one_target_resource_and_keep_job_identity() -> None:
    bound = _bound_program(
        product_count=2,
        domain_product_count=2,
        domain_call_count=2,
    )
    compiler = _DomainCompiler("tests.multi-call")

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)

    jobs = tuple(
        operation for operation in plan.coverage if isinstance(operation, RunDomainJob)
    )
    assert len({job.id for job in jobs}) == 2
    assert plan.resource_requirements == ()
    assert compiler.compile_calls == 2
    _assert_no_domain_effects(compiler)


def test_ordered_domain_calls_negotiate_one_common_compatible_prefix() -> None:
    bound = _bound_program(
        product_count=2,
        domain_product_count=2,
        domain_call_count=2,
    )
    compiler = _DomainCompiler(
        "tests.multi-call-prefix",
        compatible_sizes=(2, 1, 1, 1),
    )

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)

    jobs = tuple(
        operation for operation in plan.coverage if isinstance(operation, RunDomainJob)
    )
    assert [request.point_ordinals for request in compiler.compatibility_requests] == [
        (0, 1),
        (0, 1),
        (1,),
        (1,),
    ]
    assert [request.point_ordinals for request in compiler.compile_requests] == [
        (0,),
        (0,),
        (1,),
        (1,),
    ]
    assert [job.point_ordinals for job in jobs] == [
        (0,),
        (0,),
        (1,),
        (1,),
    ]


def test_system_rejects_a_compiler_for_a_different_target() -> None:
    compiler = _DomainCompiler("tests.target-mismatch")
    config = _config_with_domain_resources()
    mismatched = DomainTargetBinding(
        id="other.target",
        kind="tests.domain",
    )
    mismatched_config = config.model_copy(
        update={
            "system": config.system.model_copy(update={"domain_target": mismatched})
        }
    )
    bound = _bound_program(
        product_count=2,
        domain_product_count=2,
        domain_call_count=2,
        config=mismatched_config,
    )

    with pytest.raises(CheckFailed) as captured:
        ExperimentSystem(
            instrument_catalog=_catalog(bound),
            domain_compiler=compiler,
        ).compile(bound)

    assert _problem_codes(captured.value) == {"domain_target_mismatch"}


def test_system_rejects_a_compiler_for_a_different_target_kind() -> None:
    compiler = _DomainCompiler("tests.target-kind-mismatch")
    config = _config_with_domain_resources()
    mismatched = DomainTargetBinding(
        id="tests.domain.target",
        kind="tests.other-domain",
    )
    mismatched_config = config.model_copy(
        update={
            "system": config.system.model_copy(update={"domain_target": mismatched})
        }
    )
    bound = _bound_program(
        product_count=2,
        domain_product_count=2,
        domain_call_count=2,
        config=mismatched_config,
    )

    with pytest.raises(CheckFailed) as captured:
        ExperimentSystem(
            instrument_catalog=_catalog(bound),
            domain_compiler=compiler,
        ).compile(bound)

    assert _problem_codes(captured.value) == {"domain_target_kind_mismatch"}


def test_mixed_plan_preview_combines_domain_records_with_local_runtime() -> None:
    bound = _bound_program(state_mode="constant")
    compiler = _DomainCompiler("tests.preview-domain")
    provider = _TrackingProvider()
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, provider),
        domain_compiler=compiler,
    ).compile(bound)

    assert [record.id for record in plan.measurements.records] == ["record-0"]
    local_effects = plan.host
    assert local_effects is not None
    assert any(
        operation.operation
        for operation in plan.coverage
        if isinstance(operation, RunCoverageEffect)
        and isinstance(operation.operation, ApplyStateOperation)
    )
    _assert_no_domain_effects(compiler)


def test_zero_point_domain_plan_retains_direct_product_ownership() -> None:
    bound = _bound_program(point_count=0)
    compiler = _DomainCompiler("tests.zero-point")

    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)
    assert tuple(plan.coverage) == ()
    assert plan.measurements.catalog.product_use_ids == tuple(
        use.id for use in bound.bindings.product_uses
    )
    assert compiler.compile_calls == 0
    assert tuple(plan.points.points) == ()


def test_zero_point_domain_can_inspect_a_free_seed_candidate() -> None:
    bound = _bound_program(point_count=0)
    compiler = _DomainCompiler("tests.zero-point-candidate")
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(bound)

    preview = build_run_program_preview(
        plan,
        coordinates={"frequency": Quantity(5.0, "GHz")},
        coordinate_mode="free",
    )

    assert preview.total_point_count == 0
    assert preview.selected_point is not None
    assert preview.selected_point.point_index is None
    assert preview.selected_point.coordinates == {"frequency": Quantity(5.0, "GHz")}
    assert compiler.compile_calls == 1


def test_adaptive_plan_uses_an_open_point_extent_with_a_hard_limit() -> None:
    bound = _bound_program(point_count=2)
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=_DomainCompiler("tests.adaptive"),
    ).compile(
        bound,
        adaptive_domain_plan=AdaptiveDomainPlan(
            _CompleteOptimizer(),
            total_point_limit=5,
            adaptive_coordinate_ids=("frequency",),
        ),
    )

    assert plan.points.contract.point_count is None
    assert plan.points.contract.point_limit == 5
    assert len(plan.points.points) == 2
    schema = plan.measurements.schema
    assert schema is not None
    assert schema.point_domain.kind == "point_cloud"
    point_dimension = next(
        dimension for dimension in schema.dimensions if dimension.id == "point"
    )
    assert point_dimension.size is None
    preview = build_run_program_preview(plan)
    assert preview.total_point_count is None
    assert preview.initial_point_count == 2
    assert preview.point_limit == 5
    assert preview.records[0].shape[0] is None
    from scopecat.planning.preflight import (
        BoundedQuantity,
        ExactQuantity,
        summarize_preflight,
    )

    stage = summarize_preflight(
        preview,
        stage_id="adaptive",
        label="Adaptive scan",
        configuration="accepted",
        config_content_hash=plan.config_content_hash,
        configuration_meaning="Read only",
        executions=ExactQuantity(value=1, unit="runs", basis="Declared stage"),
    )
    assert stage.point_scope == "adaptive_limit"
    assert stage.initial_proposed_points == 2
    assert isinstance(stage.points_per_execution, BoundedQuantity)
    assert (stage.points_per_execution.lower, stage.points_per_execution.upper) == (
        0,
        5,
    )
    assert plan.coverage.is_durable_cut(5)
    assert not plan.coverage.is_durable_cut(-1)


@pytest.mark.parametrize("source", ("optimizer", "operator"))
def test_adaptive_coverage_batches_an_accepted_range_without_inspection(
    source: PointProposalSource,
) -> None:
    bound = _bound_program(point_count=2)
    compiler = _DomainCompiler("tests.adaptive-accept", batch_size=2)
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound),
        domain_compiler=compiler,
    ).compile(
        bound,
        adaptive_domain_plan=AdaptiveDomainPlan(
            _CompleteOptimizer(),
            total_point_limit=5,
            adaptive_coordinate_ids=("frequency",),
        ),
    )

    accepted = plan.coverage.accept_all(
        tuple(
            PointProposalAttempt(
                {"frequency": Quantity(frequency, "GHz")},
                source=source,
            )
            for frequency in (5.3, 5.5, 5.7)
        )
    )

    assert [point.ordinal for point in accepted.points] == [2, 3, 4]
    assert [point.coordinates for point in accepted.points] == [
        {"frequency": Quantity(5.3, "GHz")},
        {"frequency": Quantity(5.5, "GHz")},
        {"frequency": Quantity(5.7, "GHz")},
    ]
    assert compiler.compile_requests == []
    operations = tuple(accepted.operations)
    assert [request.point_ordinals for request in compiler.compile_requests] == [
        (2, 3),
        (4,),
    ]
    assert [request.batch_ordinal for request in compiler.compile_requests] == [2, 3]
    assert not any(
        request.inspection_requested for request in compiler.compile_requests
    )
    assert all(
        isinstance(operation, RunCoverageCheckpoint | RunDomainJob)
        for operation in operations
    )


@pytest.mark.parametrize("domain_before_state", [False, True])
def test_planned_settings_follow_the_selected_frozen_point_and_free_candidate(
    domain_before_state: bool,
) -> None:
    bound = _bound_program(
        point_count=3, state_mode="varying", domain_before_state=domain_before_state
    )
    compiler = _DomainCompiler("tests.planned-settings", batch_size=32)
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, TestSignalInstrumentProvider()),
        domain_compiler=compiler,
    ).compile(bound)
    preview = build_run_program_preview(plan, point=1)
    [setting] = preview.planned_settings
    assert setting.point_index == 1
    assert setting.setting.target.property_id == "frequency"
    assert setting.setting.value.root == Quantity(5.1, "GHz")
    assert setting.operation_index == int(domain_before_state)
    assert setting.assignment_index == 0
    assert compiler.compile_calls == 1
    _assert_no_domain_effects(compiler)
    candidate = build_run_program_preview(
        plan, coordinates={"frequency": Quantity(5.25, "GHz")}, coordinate_mode="free"
    )
    [changed] = candidate.planned_settings
    assert candidate.selected_point is not None
    assert changed.point_index is None
    assert changed.proposal_fingerprint == candidate.selected_point.proposal_fingerprint
    assert changed.setting.value.root == Quantity(5.25, "GHz")
    assert setting.setting.value.root == Quantity(5.1, "GHz")
    assert compiler.compile_calls == 2
    _assert_no_domain_effects(compiler)


def test_planned_settings_budget_preserves_jobs_and_stream_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scopecat.planning.system as system_module

    original = system_module._validated_coverage
    observed: list[RunCoveredOperation] = []

    def wide_settings(
        operations: Iterator[RunCoveredOperation],
        *,
        validator: system_module._CoverageValidator,
    ) -> Iterator[RunCoveredOperation]:
        for operation in original(operations, validator=validator):
            observed.append(operation)
            if isinstance(operation, RunCoverageEffect) and isinstance(
                operation.operation, ApplyStateOperation
            ):
                yield replace(
                    operation,
                    operation=replace(
                        operation.operation, targets=operation.operation.targets * 65
                    ),
                )
            else:
                yield operation

    monkeypatch.setattr(system_module, "_validated_coverage", wide_settings)
    bound = _bound_program(state_mode="constant")
    compiler = _DomainCompiler("tests.setting-budget", batch_size=32)
    plan = ExperimentSystem(
        instrument_catalog=_catalog(bound, TestSignalInstrumentProvider()),
        domain_compiler=compiler,
    ).compile(bound)
    inspected = plan.coverage.inspect(0)
    assert inspected is not None
    assert inspected.planned_settings_truncated
    assert len(inspected.planned_settings) == inspected.planned_setting_limit == 64
    assert [setting.assignment_index for setting in inspected.planned_settings] == list(
        range(64)
    )
    assert len(inspected.jobs) == 1
    assert sum(isinstance(operation, RunDomainJob) for operation in observed) == 1
    assert compiler.compile_calls == 1
    _assert_no_domain_effects(compiler)
