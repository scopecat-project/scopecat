"""Compile bound experiment semantics for one physical experiment system.

This boundary coordinates local target selection, domain lowering, and bounded
coverage so placement decisions share one view of effect order and resource
ownership. Its output admits static resource authority while compiling domain
batches lazily at the execution boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from typing import cast

from scopecat.compiler.bind import BoundDomainTarget, BoundPlan
from scopecat.execution.local.program import (
    ApplyStateOperation,
    ComputeOperation,
    InvokeOperation,
    LocalOperation,
)
from scopecat.execution.program import (
    RunAcceptedCoverage,
    RunCoverage,
    RunCoverageCheckpoint,
    RunCoverageEffect,
    RunCoveredOperation,
    RunDomainJob,
    RunHostBinding,
    RunPointInspection,
    RunProgram,
)
from scopecat.inspection import (
    PLANNED_INSTRUMENT_SETTING_LIMIT,
    CompiledProgramInspectionQuery,
    PlannedInstrumentSetting,
)
from scopecat.kernel.errors import CheckFailed, ProviderContractError
from scopecat.kernel.points import AcceptedRunPoint, PointProposalAttempt
from scopecat.kernel.problems import (
    Problem,
    ProblemPhase,
    model_location,
    problem,
)
from scopecat.kernel.product_identity import ProductUseId
from scopecat.kernel.resource_identity import (
    DomainTargetRequirement,
    ResourceRequirement,
)
from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.kernel.value_identity import scalar_values_equal
from scopecat.measurements.projection import select_measurement_projection
from scopecat.measurements.records import EntityAcquisitionCohortPlan
from scopecat.optimization import AdaptiveDomainPlan
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.domain_bridge import (
    make_domain_batch_request,
    make_domain_call_view,
)
from scopecat.planning.domain_results import (
    domain_result_product_use_ids,
)
from scopecat.planning.local_effects import (
    LocalTargetPlan,
    MaterializedLocalEffects,
    local_operation_resource_requirements,
)
from scopecat.planning.local_materialization import (
    local_execution_is_point_invariant,
    materialize_local_execution,
    materialize_local_success_state,
    prepare_local_target,
)
from scopecat.planning.measurement_projection import (
    project_measurement_catalog,
    project_run_point_catalog,
    project_static_value_record_candidates,
)
from scopecat.planning.point_materialization import (
    MaterializedBoundPoints,
    append_candidate_bound_points,
    prepare_bound_points,
    prepare_candidate_bound_points,
)
from scopecat.planning.point_order import (
    PointExecutionGroup,
    PointExecutionPlan,
    resolve_point_schedule,
)
from scopecat.planning.provider_binding import (
    validate_run_host_binding,
)
from scopecat.program.logical import LogicalDomainExecution, LogicalEffect
from scopecat.records.config import (
    ConfigProfileSnapshot,
    config_content_hash,
)
from scopecat.records.instrument import (
    InstrumentStateSetting,
    InterfaceStateMemberTarget,
)
from scopecat.sdk.domain.compiler import (
    DomainBatchCandidate,
    DomainBatchPreparationCost,
    DomainBatchPreparationLimits,
    DomainCompiler,
)
from scopecat.sdk.domain.execution import DomainStateAddress, PreparedDomainExecution
from scopecat.sdk.domain.view import DomainCallView
from scopecat.sdk.instruments.contracts import (
    resolve_implementation_component,
    resolve_implementation_state_reference,
)
from scopecat.sdk.payloads import PayloadCodecRegistry

_INITIAL_LOCAL_COVERAGE_BATCH_SIZE = 32
_MAX_LOCAL_COVERAGE_BATCH_SIZE = 256


@dataclass(slots=True)
class _AcceptedBoundPointState:
    """Shared materialized point prefix used by compilation and projection."""

    current: MaterializedBoundPoints


@dataclass(frozen=True, slots=True)
class ExperimentSystem:
    """The physical interfaces used to lower one experiment definition.

    The daemon-resolved instrument catalog is immutable planning input. Domain
    compilers and payload codecs remain local code because they lower and encode
    author-authored values without opening instrument connections.
    """

    instrument_catalog: InstrumentContractCatalog = field(repr=False)
    domain_compiler: DomainCompiler | None = field(default=None, repr=False)
    payload_codecs: PayloadCodecRegistry = field(
        default_factory=PayloadCodecRegistry,
        repr=False,
        compare=False,
    )

    def compile(
        self,
        bound: BoundPlan,
        *,
        adaptive_domain_plan: AdaptiveDomainPlan | None = None,
    ) -> RunProgram:
        return _compile_system_program(
            system=self,
            bound=bound,
            adaptive_domain_plan=adaptive_domain_plan,
        )


type ExperimentSystemBuilder = Callable[
    [ConfigProfileSnapshot, InstrumentContractCatalog],
    ExperimentSystem,
]


def build_experiment_system(
    builder: ExperimentSystemBuilder | None,
    config: ConfigProfileSnapshot,
    instrument_catalog: InstrumentContractCatalog,
) -> ExperimentSystem:
    """Build a config-bound experiment system at the planning boundary."""

    if builder is None:
        return ExperimentSystem(instrument_catalog=instrument_catalog)
    system = builder(config, instrument_catalog)
    if system.instrument_catalog != instrument_catalog:
        raise ValueError(
            "experiment system builder must retain the daemon instrument catalog"
        )
    return system


def _compile_system_program(
    *,
    system: ExperimentSystem,
    bound: BoundPlan,
    adaptive_domain_plan: AdaptiveDomainPlan | None,
) -> RunProgram:
    config = bound.environment.config
    catalog = system.instrument_catalog
    expected_config_hash = config_content_hash(config)
    if catalog.config_content_hash != expected_config_hash:
        raise ProviderContractError(
            (
                problem(
                    "instrument_catalog_config_mismatch",
                    "instrument contracts do not match the planning configuration",
                    phase=ProblemPhase.PROVIDER_PREFLIGHT,
                    location=model_location(
                        "instrument_catalog",
                        "config_content_hash",
                    ),
                    details={
                        "expected": expected_config_hash,
                        "actual": catalog.config_content_hash,
                    },
                ),
            )
        )
    domain_use_ids_by_execution = {
        execution.id: domain_result_product_use_ids(bound.bindings, execution)
        for execution in bound.program.program.domain_executions
    }
    domain_calls = {
        execution_id: make_domain_call_view(
            bound,
            execution_id,
            product_use_ids,
        )
        for execution_id, product_use_ids in domain_use_ids_by_execution.items()
    }
    _validate_domain_compiler(
        system.domain_compiler,
        target=bound.domain_target,
    )
    domain_owned_product_use_ids = frozenset(
        use_id
        for product_use_ids in domain_use_ids_by_execution.values()
        for use_id in product_use_ids
    )
    compute_output_use_ids = frozenset(
        use_id
        for compute in bound.bindings.measurement_computes
        for output in compute.outputs
        for use_id in output.product_use_ids
    )
    local_product_use_ids = tuple(
        use.id
        for use in bound.bindings.product_uses
        if use.id not in domain_owned_product_use_ids
        and use.id not in compute_output_use_ids
    )
    local_instrument_required = bool(
        local_product_use_ids
        or bound.program.program.bindings
        or bound.program.program.invocations
    )
    local_execution_required = bool(
        local_instrument_required or bound.bindings.live_compute_ids
    )
    implementation_problems = list(
        _implementation_problems(
            has_domain_call=bool(bound.program.program.domain_executions),
            has_domain_compiler=system.domain_compiler is not None,
            local_instrument_required=local_instrument_required,
            domain_instrument_required=False,
            has_local_instrument_catalog=catalog.provider_id is not None,
        )
    )
    if implementation_problems:
        raise CheckFailed(implementation_problems)
    if local_instrument_required and catalog.problems:
        raise ProviderContractError(catalog.problems)
    bound_points = prepare_bound_points(bound)
    accepted_bound_points = _AcceptedBoundPointState(bound_points)
    point_domain = bound_points.point_domain
    point_limit = (
        None if adaptive_domain_plan is None else adaptive_domain_plan.total_point_limit
    )
    if adaptive_domain_plan is not None:
        adaptive_domain_plan.validate_initial_point_count(len(point_domain.points))
    logical = bound.program.program
    execution_plan = resolve_point_schedule(
        point_domain,
        repeat=logical.point_repeat,
        repeat_mode=logical.point_repeat_mode,
        schedule=logical.point_schedule,
    )
    execution_ordinals = execution_plan.ordinals
    if adaptive_domain_plan is not None and logical.point_schedule.grouping is not None:
        grouping = logical.point_schedule.grouping
        raise CheckFailed(
            [
                _planning_problem(
                    "adaptive_point_grouping_unsupported",
                    "adaptive point plans cannot yet restart coordinate groups "
                    "as a unit",
                    details={"grouping_id": grouping.id},
                )
            ]
        )
    measurement_catalog = project_measurement_catalog(
        bound_points,
        point_limit=point_limit,
    )
    point_catalog = project_run_point_catalog(
        bound_points,
        point_limit=point_limit,
    )
    measurements = select_measurement_projection(
        measurement_catalog,
        bound.bindings.record_uses,
        result_fields=logical.result_fields,
        static_value_source=lambda points: project_static_value_record_candidates(
            accepted_bound_points.current,
            tuple(
                binding.value_id
                for compute in bound.bindings.measurement_computes
                for binding in compute.value_inputs
            ),
            point_ordinals=tuple(point.ordinal for point in points),
        ),
    )
    _validate_entity_acquisition_cohort_placement(
        measurements.acquisition_cohorts,
        local_product_use_ids=frozenset(local_product_use_ids),
    )
    local_target = (
        prepare_local_target(
            bound,
            product_use_ids=frozenset(local_product_use_ids),
            instrument_order=tuple(item.instrument_id for item in catalog.instruments),
            acquisition_cohorts=measurements.acquisition_cohorts,
            payload_codecs=system.payload_codecs,
        )
        if local_execution_required
        else None
    )
    initial_local_probe = (
        _InitialLocalProbe(
            ordinal=execution_ordinals[0],
            point_uid=bound_points.point_domain.points[
                execution_ordinals[0]
            ].logical_id.value,
            effects=materialize_local_execution(
                bound_points,
                target=local_target,
                point_ordinals=(execution_ordinals[0],),
            ),
            point_invariant=local_execution_is_point_invariant(local_target),
        )
        if local_target is not None and execution_ordinals
        else None
    )
    local_success_state = (
        materialize_local_success_state(
            bound,
            target=local_target,
        )
        if local_target is not None
        else ()
    )
    local_requirements = _local_target_resource_requirements(
        local_target,
        success_state=local_success_state,
    )
    coverage = _compile_coverage(
        system=system,
        bound=bound,
        bound_points=bound_points,
        execution_plan=execution_plan,
        adaptive=adaptive_domain_plan is not None,
        domain_calls=domain_calls,
        local_target=local_target,
        initial_local_probe=initial_local_probe,
        catalog=catalog,
        accepted_bound_points=accepted_bound_points,
    )
    domain_instrument_ids = (
        ()
        if system.domain_compiler is None or not domain_calls
        else system.domain_compiler.instrument_ids
    )
    domain_target_requirement = _domain_target_requirement(
        bound.domain_target,
        instrument_ids=domain_instrument_ids,
    )
    domain_footprint = _domain_target_footprint(domain_target_requirement)
    domain_instrument_required = bool(domain_instrument_ids)
    domain_implementation_problems = _implementation_problems(
        has_domain_call=False,
        has_domain_compiler=True,
        local_instrument_required=False,
        domain_instrument_required=domain_instrument_required,
        has_local_instrument_catalog=catalog.provider_id is not None,
    )
    if domain_implementation_problems:
        raise CheckFailed(domain_implementation_problems)
    if domain_instrument_required and catalog.problems:
        raise ProviderContractError(catalog.problems)
    resource_requirements = _sorted_requirements(
        (*local_requirements, *domain_footprint)
    )
    local_instrument_ids = frozenset(
        requirement.id for requirement in local_requirements
    )
    host = _host_binding(
        catalog,
        instrument_ids=local_instrument_ids | frozenset(domain_instrument_ids),
        payload_codecs=system.payload_codecs,
    )
    if host is not None:
        validate_run_host_binding(
            host=host,
            effect_blocks=(
                ()
                if initial_local_probe is None
                else (
                    tuple(
                        effect.operation
                        for effect in initial_local_probe.effects.compute_operations
                    ),
                    *(
                        tuple(effect.operation for effect in effects)
                        for effects in initial_local_probe.effects.effect_operations
                    ),
                    local_success_state,
                )
            ),
            problems=(),
        )
    return RunProgram(
        config_content_hash=config_content_hash(config),
        host=host,
        coverage=coverage,
        success_state=local_success_state,
        points=point_catalog,
        measurements=measurements,
        point_schedule=logical.point_schedule,
        point_groups=execution_plan.groups,
        measurement_computes=bound.bindings.measurement_computes,
        preview_compute_operations=(
            ()
            if initial_local_probe is None
            else tuple(
                cast("ComputeOperation", effect.operation)
                for effect in initial_local_probe.effects.compute_operations
            )
        ),
        resource_requirements=resource_requirements,
        domain_target_requirement=domain_target_requirement,
        adaptive_domain_plan=adaptive_domain_plan,
    )


def _host_binding(
    catalog: InstrumentContractCatalog,
    *,
    instrument_ids: frozenset[str],
    payload_codecs: PayloadCodecRegistry,
) -> RunHostBinding | None:
    provider_id = catalog.provider_id
    if provider_id is None or not instrument_ids:
        return None
    instrument_order = tuple(
        description.instrument_id
        for description in catalog.instruments
        if description.instrument_id in instrument_ids
    )
    return RunHostBinding(
        resource_order=instrument_order,
        provider_id=provider_id,
        advertised_descriptions={
            description.instrument_id: description
            for description in catalog.instruments
        },
        payload_codecs=payload_codecs,
    )


def _validate_domain_compiler(
    compiler: DomainCompiler | None,
    *,
    target: BoundDomainTarget | None,
) -> None:
    if target is None or compiler is None:
        return
    if compiler.target_id != target.id:
        raise CheckFailed(
            [
                _planning_problem(
                    "domain_target_mismatch",
                    "the domain compiler target does not match the bound target",
                    details={
                        "compiler_target_id": compiler.target_id,
                        "configured_target_id": target.id,
                    },
                )
            ]
        )
    if compiler.target_kind != target.kind:
        raise CheckFailed(
            [
                _planning_problem(
                    "domain_target_kind_mismatch",
                    "the domain compiler adapter does not match the bound target",
                    details={
                        "compiler_target_kind": compiler.target_kind,
                        "configured_target_kind": target.kind,
                    },
                )
            ]
        )


def _domain_target_requirement(
    target: BoundDomainTarget | None,
    *,
    instrument_ids: tuple[str, ...],
) -> DomainTargetRequirement | None:
    if target is None:
        return None
    unauthorized = sorted(set(instrument_ids) - set(target.instrument_ids))
    if unauthorized:
        raise CheckFailed(
            [
                _planning_problem(
                    "domain_target_instrument_unauthorized",
                    "the compiled domain execution requires instruments outside "
                    "the configured target authority",
                    details={"instrument_ids": unauthorized},
                )
            ]
        )
    return DomainTargetRequirement(
        id=target.id,
        kind=target.kind,
        instrument_ids=instrument_ids,
    )


def _domain_target_footprint(
    target: DomainTargetRequirement | None,
) -> tuple[ResourceRequirement, ...]:
    if target is None:
        return ()
    return _sorted_requirements(
        tuple(
            ResourceRequirement(instrument_id, "instrument")
            for instrument_id in target.instrument_ids
        )
    )


def _sorted_requirements(
    requirements: tuple[ResourceRequirement, ...],
) -> tuple[ResourceRequirement, ...]:
    return tuple(
        sorted(
            set(requirements),
            key=lambda requirement: (requirement.kind, requirement.id),
        )
    )


def _local_target_resource_requirements(
    local_target: LocalTargetPlan | None,
    *,
    success_state: Sequence[LocalOperation] = (),
) -> tuple[ResourceRequirement, ...]:
    target_instrument_ids = (
        ()
        if local_target is None
        else tuple(
            instrument_id
            for manifest in local_target.resource_ports.values()
            for instrument_id in manifest.candidate_instrument_ids
        )
    )
    return _sorted_requirements(
        (
            *(ResourceRequirement(item) for item in target_instrument_ids),
            *(
                requirement
                for operation in success_state
                for requirement in local_operation_resource_requirements(operation)
            ),
        )
    )


def _state_address_description(address: DomainStateAddress) -> str:
    component = "/".join(address.component_path)
    mounted_interface = (
        f"{address.interface_id}/{component}" if component else address.interface_id
    )
    return f"{address.instrument_id}:{mounted_interface}.{address.property_id}"


@dataclass(frozen=True, slots=True)
class _ScheduledStateGuarantee:
    value: StateValue
    provenance: dict[str, object]


@dataclass(frozen=True, slots=True)
class _MaterializedLocalCoverage:
    effects: MaterializedLocalEffects


@dataclass(frozen=True, slots=True)
class _InitialLocalProbe:
    ordinal: int
    point_uid: str
    effects: MaterializedLocalEffects
    point_invariant: bool


class _CoverageValidator:
    """Validate batch-local contracts immediately before yielding each operation."""

    def __init__(
        self,
        *,
        domain_instrument_ids: tuple[str, ...],
        catalog: InstrumentContractCatalog,
    ) -> None:
        self._target_instrument_ids = frozenset(domain_instrument_ids)
        self._catalog = catalog
        self._guarantees: dict[DomainStateAddress, _ScheduledStateGuarantee] = {}
        self._invalidated_by: dict[DomainStateAddress, dict[str, object]] = {}
        self._host_writes_by_point: dict[int, set[DomainStateAddress]] = {}
        self._domain_writes_by_point: dict[int, set[DomainStateAddress]] = {}

    def register_local_coverage(self, effects: MaterializedLocalEffects) -> None:
        for group in effects.effect_operations:
            for covered in group:
                operation = covered.operation
                if not isinstance(operation, ApplyStateOperation):
                    continue
                writes = self._host_writes_by_point.setdefault(
                    covered.point_index,
                    set(),
                )
                writes.update(
                    DomainStateAddress(
                        instrument_id=operation.instrument_id,
                        interface_id=target.interface_id,
                        component_path=target.component_path,
                        property_id=target.property_id,
                    )
                    for target in operation.targets
                )

    def validate(self, covered: RunCoveredOperation) -> None:
        if isinstance(covered, RunCoverageCheckpoint):
            for point_index in covered.point_indices:
                self._host_writes_by_point.pop(point_index, None)
                self._domain_writes_by_point.pop(point_index, None)
            return
        if isinstance(covered, RunCoverageEffect) and isinstance(
            operation := covered.operation,
            ApplyStateOperation,
        ):
            self._record_host_state(covered, operation)
            return
        if isinstance(covered, RunCoverageEffect) and isinstance(
            operation := covered.operation,
            InvokeOperation,
        ):
            self._record_host_invalidation(covered, operation)
            return
        if isinstance(covered, RunDomainJob):
            self._validate_domain_job(covered)

    def _record_host_state(
        self,
        covered: RunCoverageEffect,
        operation: ApplyStateOperation,
    ) -> None:
        for target in operation.targets:
            address = DomainStateAddress(
                instrument_id=operation.instrument_id,
                interface_id=target.interface_id,
                component_path=target.component_path,
                property_id=target.property_id,
            )
            domain_writes = self._domain_writes_by_point.get(covered.point_index)
            if domain_writes is not None and address in domain_writes:
                self._raise_state_write_conflict((address,))
            self._host_writes_by_point.setdefault(covered.point_index, set()).add(
                address
            )
            self._guarantees[address] = _ScheduledStateGuarantee(
                value=target.value,
                provenance={"kind": "host_state", "point_index": covered.point_index},
            )
            self._invalidated_by.pop(address, None)

    def _record_host_invalidation(
        self,
        covered: RunCoverageEffect,
        operation: InvokeOperation,
    ) -> None:
        provenance: dict[str, object] = {
            "kind": "host_operation",
            "instrument_id": operation.instrument_id,
            "interface_id": operation.interface_id,
            "component_path": operation.component_path,
            "operation_id": operation.operation_id,
            "point_index": covered.point_index,
        }
        for invalidated in _invoke_state_invalidations(operation, self._catalog):
            self._guarantees.pop(invalidated, None)
            self._invalidated_by[invalidated] = provenance

    def _validate_domain_job(self, job: RunDomainJob) -> None:
        unauthorized = sorted(
            set(job.execution.instrument_ids) - self._target_instrument_ids
        )
        if unauthorized:
            raise CheckFailed(
                [
                    _planning_problem(
                        "domain_target_instrument_unauthorized",
                        "the compiled domain batch requires instruments outside the "
                        "reserved domain footprint",
                        details={"instrument_ids": unauthorized},
                    )
                ]
            )
        writes = tuple(
            dict.fromkeys(
                (
                    *job.execution.setup_write_footprint,
                    *job.execution.realtime_write_footprint,
                )
            )
        )
        conflicts: list[DomainStateAddress] = []
        for write in writes:
            if any(
                (host_writes := self._host_writes_by_point.get(ordinal)) is not None
                and write in host_writes
                for ordinal in job.point_ordinals
            ):
                conflicts.append(write)
        if conflicts:
            self._raise_state_write_conflict(tuple(sorted(conflicts)))
        for ordinal in job.point_ordinals:
            self._domain_writes_by_point.setdefault(ordinal, set()).update(writes)
        problems = self._domain_requirement_problems(job)
        if problems:
            raise CheckFailed(problems)
        self._record_domain_state(job)

    def _raise_state_write_conflict(
        self,
        conflicts: tuple[DomainStateAddress, ...],
    ) -> None:
        raise CheckFailed(
            [
                _planning_problem(
                    "host_domain_state_write_conflict",
                    "host and domain effects cannot both own the same physical state "
                    "property",
                    details={
                        "state_addresses": tuple(
                            _state_address_description(address) for address in conflicts
                        )
                    },
                )
            ]
        )

    def _domain_requirement_problems(self, job: RunDomainJob) -> list[Problem]:
        problems: list[Problem] = []
        for requirement in job.execution.state_requirements:
            actual = self._guarantees.get(requirement.address)
            if actual is None:
                details: dict[str, object] = {
                    "domain_job_id": job.id,
                    "state_address": _state_address_description(requirement.address),
                    "point_ordinals": job.point_ordinals,
                }
                if invalidation := self._invalidated_by.get(requirement.address):
                    details["invalidated_by"] = invalidation
                problems.append(
                    _planning_problem(
                        "domain_state_requirement_missing",
                        "domain execution requires physical state that no preceding "
                        "host stage guarantees",
                        details=details,
                    )
                )
            elif not _state_values_equal(actual.value, requirement.value):
                problems.append(
                    _planning_problem(
                        "domain_state_requirement_mismatch",
                        "domain execution requires a different physical state value "
                        "than the preceding host guarantee",
                        details={
                            "domain_job_id": job.id,
                            "state_address": _state_address_description(
                                requirement.address
                            ),
                            "point_ordinals": job.point_ordinals,
                            "guaranteed_by": actual.provenance,
                        },
                    )
                )
        return problems

    def _record_domain_state(self, job: RunDomainJob) -> None:
        invalidations = (
            (job.execution.setup_write_footprint, "domain_setup_write_footprint"),
            (job.execution.setup_state_invalidations, "domain_setup_invalidation"),
        )
        for addresses, kind in invalidations:
            self._record_domain_invalidations(job, addresses, kind=kind)
        for requirement in job.execution.state_requirements:
            self._guarantees[requirement.address] = _ScheduledStateGuarantee(
                value=requirement.value,
                provenance={
                    "kind": "domain_requirement_reconciliation",
                    "domain_job_id": job.id,
                    "point_ordinals": job.point_ordinals,
                },
            )
            self._invalidated_by.pop(requirement.address, None)
        invalidations = (
            (job.execution.realtime_write_footprint, "domain_realtime_write_footprint"),
            (
                job.execution.realtime_state_invalidations,
                "domain_realtime_invalidation",
            ),
        )
        for addresses, kind in invalidations:
            self._record_domain_invalidations(job, addresses, kind=kind)

    def _record_domain_invalidations(
        self,
        job: RunDomainJob,
        addresses: tuple[DomainStateAddress, ...],
        *,
        kind: str,
    ) -> None:
        for address in addresses:
            self._guarantees.pop(address, None)
            self._invalidated_by[address] = {
                "kind": kind,
                "domain_job_id": job.id,
                "point_ordinals": job.point_ordinals,
            }


def _validated_coverage(
    operations: Iterable[RunCoveredOperation | _MaterializedLocalCoverage],
    *,
    validator: _CoverageValidator,
) -> Iterator[RunCoveredOperation]:
    for operation in operations:
        if isinstance(operation, _MaterializedLocalCoverage):
            validator.register_local_coverage(operation.effects)
            continue
        validator.validate(operation)
        yield operation


def _invoke_state_invalidations(
    operation: InvokeOperation,
    catalog: InstrumentContractCatalog,
) -> tuple[DomainStateAddress, ...]:
    description = next(
        item
        for item in catalog.instruments
        if item.instrument_id == operation.instrument_id
    )
    interface_spec = next(
        item for item in description.interfaces if item.id == operation.interface_id
    )
    component_spec = resolve_implementation_component(
        description,
        interface_spec,
        operation.component_path,
    )
    assert component_spec is not None
    operation_spec = next(
        item for item in component_spec.operations if item.id == operation.operation_id
    )
    resolved = tuple(
        resolve_implementation_state_reference(
            description,
            reference,
            context_interface_id=operation.interface_id,
            context_component_path=operation.component_path,
        )
        for reference in operation_spec.invalidates
    )
    if any(reference is None for reference in resolved):
        raise ValueError(
            f"operation {operation.operation_id!r} has an invalid state "
            "invalidation mount"
        )
    return tuple(
        DomainStateAddress(
            instrument_id=operation.instrument_id,
            interface_id=reference.interface_id,
            component_path=tuple(reference.component_path),
            property_id=reference.property_id,
        )
        for reference in resolved
        if reference is not None
    )


def _state_values_equal(left: StateValue, right: StateValue) -> bool:
    left_value = left.root
    right_value = right.root
    if isinstance(left_value, PayloadRef) or isinstance(right_value, PayloadRef):
        return left_value == right_value
    return scalar_values_equal(left_value, right_value)


def _compile_coverage(
    *,
    system: ExperimentSystem,
    bound: BoundPlan,
    bound_points: MaterializedBoundPoints,
    execution_plan: PointExecutionPlan,
    adaptive: bool,
    domain_calls: dict[str, DomainCallView],
    local_target: LocalTargetPlan | None,
    initial_local_probe: _InitialLocalProbe | None,
    catalog: InstrumentContractCatalog,
    accepted_bound_points: _AcceptedBoundPointState,
) -> RunCoverage:
    compiler = system.domain_compiler

    def recovery_operations(
        start_point_index: int,
        completed_group_ids: frozenset[str],
    ) -> Iterator[RunCoveredOperation]:
        selected_groups = tuple(
            group
            for group in execution_plan.remaining_groups(
                durable_start=start_point_index
            )
            if group.id not in completed_group_ids
        )
        if not selected_groups:
            return iter(())
        return _validated_coverage(
            _coverage_operations(
                compiler=compiler,
                bound_points=bound_points,
                point_groups=selected_groups,
                effects=bound.program.program.effects,
                domain_calls=domain_calls,
                local_target=local_target,
                initial_local_probe=(
                    initial_local_probe
                    if initial_local_probe is not None
                    and (
                        initial_local_probe.point_invariant
                        or any(
                            initial_local_probe.ordinal in group.ordinals
                            for group in selected_groups
                        )
                    )
                    else None
                ),
                initial_batch_ordinal=start_point_index,
            ),
            validator=_CoverageValidator(
                domain_instrument_ids=(
                    () if compiler is None else compiler.instrument_ids
                ),
                catalog=catalog,
            ),
        )

    def operations(start_point_index: int) -> Iterator[RunCoveredOperation]:
        return recovery_operations(start_point_index, frozenset())

    def inspect(
        point: int | PointProposalAttempt,
        inspection_query: CompiledProgramInspectionQuery | None,
    ) -> RunPointInspection:
        if isinstance(point, int):
            if not 0 <= point < execution_plan.point_count:
                raise IndexError(point)
            point_index: int | None = point
            candidate = PointProposalAttempt(
                coordinates=bound_points.point_domain.points[point].row,
            )
            selected_bound_points = bound_points
            selected_ordinal = point
        else:
            point_index = None
            candidate, selected_bound_points = prepare_candidate_bound_points(
                bound_points,
                point,
            )
            selected_ordinal = 0
        inspection_group = PointExecutionGroup(
            id="inspection",
            key=candidate.coordinates,
            ordinals=(selected_ordinal,),
        )
        selected_operations = _validated_coverage(
            _coverage_operations(
                compiler=compiler,
                bound_points=selected_bound_points,
                point_groups=(inspection_group,),
                effects=bound.program.program.effects,
                domain_calls=domain_calls,
                local_target=local_target,
                initial_local_probe=(
                    initial_local_probe
                    if initial_local_probe is not None
                    and (
                        initial_local_probe.point_invariant
                        or (
                            point_index is not None
                            and initial_local_probe.ordinal == point_index
                        )
                    )
                    else None
                ),
                inspection_requested=True,
                inspection_query=inspection_query,
            ),
            validator=_CoverageValidator(
                domain_instrument_ids=(
                    () if compiler is None else compiler.instrument_ids
                ),
                catalog=catalog,
            ),
        )
        jobs: list[RunDomainJob] = []
        settings: list[PlannedInstrumentSetting] = []
        settings_truncated = False
        for operation_index, operation in enumerate(selected_operations):
            if isinstance(operation, RunDomainJob):
                jobs.append(operation)
            elif isinstance(operation, RunCoverageEffect) and isinstance(
                operation.operation, ApplyStateOperation
            ):
                state = operation.operation
                for assignment_index, target in enumerate(state.targets):
                    if len(settings) == PLANNED_INSTRUMENT_SETTING_LIMIT:
                        settings_truncated = True
                        break
                    settings.append(
                        PlannedInstrumentSetting(
                            point_index=point_index,
                            proposal_fingerprint=candidate.proposal_fingerprint,
                            operation_index=operation_index,
                            assignment_index=assignment_index,
                            operation_id=state.operation_id,
                            instrument_id=state.instrument_id,
                            setting=InstrumentStateSetting(
                                target=InterfaceStateMemberTarget(
                                    interface_id=target.interface_id,
                                    component_path=target.component_path,
                                    property_id=target.property_id,
                                ),
                                value=target.value,
                            ),
                        )
                    )
        return RunPointInspection(
            point_index=point_index,
            candidate=candidate,
            jobs=tuple(jobs),
            planned_settings=tuple(settings),
            planned_settings_truncated=settings_truncated,
        )

    def accept_all(
        candidates: tuple[PointProposalAttempt, ...],
    ) -> RunAcceptedCoverage:
        resolved, extended_bound_points = append_candidate_bound_points(
            accepted_bound_points.current,
            candidates,
        )
        start = len(accepted_bound_points.current.point_domain.points)
        point_ordinals = tuple(range(start, start + len(resolved)))
        accepted_points = tuple(
            AcceptedRunPoint.accept(
                candidate,
                logical_id=extended_bound_points.point_domain.points[
                    ordinal
                ].logical_id,
            )
            for ordinal, candidate in zip(point_ordinals, resolved, strict=True)
        )
        operations = _validated_coverage(
            _coverage_operations(
                compiler=compiler,
                bound_points=extended_bound_points,
                point_groups=tuple(
                    PointExecutionGroup(
                        id=f"adaptive-{ordinal}",
                        key=accepted.coordinates,
                        ordinals=(ordinal,),
                    )
                    for ordinal, accepted in zip(
                        point_ordinals,
                        accepted_points,
                        strict=True,
                    )
                ),
                effects=bound.program.program.effects,
                domain_calls=domain_calls,
                local_target=local_target,
                initial_local_probe=(
                    initial_local_probe
                    if initial_local_probe is not None
                    and initial_local_probe.point_invariant
                    else None
                ),
                initial_batch_ordinal=start,
            ),
            validator=_CoverageValidator(
                domain_instrument_ids=(
                    () if compiler is None else compiler.instrument_ids
                ),
                catalog=catalog,
            ),
        )
        accepted_bound_points.current = extended_bound_points
        return RunAcceptedCoverage(points=accepted_points, operations=operations)

    return RunCoverage(
        operations,
        inspect=inspect,
        accept_all=accept_all,
        is_durable_cut=(
            _nonnegative_point_count_is_durable
            if adaptive
            else execution_plan.is_durable_cut
        ),
        resume_factory=recovery_operations,
    )


def _nonnegative_point_count_is_durable(point_count: int) -> bool:
    return point_count >= 0


def _coverage_operations(
    *,
    compiler: DomainCompiler | None,
    bound_points: MaterializedBoundPoints,
    point_groups: Sequence[PointExecutionGroup],
    effects: tuple[LogicalEffect, ...],
    domain_calls: dict[str, DomainCallView],
    local_target: LocalTargetPlan | None,
    initial_local_probe: _InitialLocalProbe | None,
    initial_batch_ordinal: int = 0,
    inspection_requested: bool = False,
    inspection_query: CompiledProgramInspectionQuery | None = None,
) -> Iterator[RunCoveredOperation | _MaterializedLocalCoverage]:
    next_batch_ordinals = {
        effect.id: initial_batch_ordinal
        for effect in effects
        if isinstance(effect, LogicalDomainExecution)
    }
    previous_static_frame: tuple[tuple[ApplyStateOperation, ...], ...] | None = None
    has_previous_static_frame = False
    has_domain_calls = bool(domain_calls)
    point_ordinals = tuple(
        ordinal for group in point_groups for ordinal in group.ordinals
    )
    group_checkpoints = _point_group_checkpoint_offsets(point_groups)
    total_point_count = len(point_ordinals)
    if not total_point_count:
        return
    max_candidate_retained_bytes: int | None = None
    if has_domain_calls:
        assert compiler is not None
        preparation_limits = _domain_batch_preparation_limits(
            compiler,
            total_point_count,
        )
        next_batch_max_points = preparation_limits.max_points
        max_candidate_retained_bytes = preparation_limits.max_retained_bytes
    else:
        next_batch_max_points = min(
            total_point_count,
            _INITIAL_LOCAL_COVERAGE_BATCH_SIZE,
        )
    covered_point_count = 0
    while covered_point_count < total_point_count:
        candidate_point_count = _preferred_candidate_point_count(
            group_checkpoints,
            start=covered_point_count,
            remaining=total_point_count - covered_point_count,
            max_points=next_batch_max_points,
        )
        candidate_batch = point_ordinals[
            covered_point_count : covered_point_count + candidate_point_count
        ]
        candidate_cuts = tuple(range(1, len(candidate_batch) + 1))
        batch_candidates: dict[str, DomainBatchCandidate] = {}
        if has_domain_calls:
            assert compiler is not None
            for effect in effects:
                if isinstance(effect, LogicalDomainExecution):
                    assert max_candidate_retained_bytes is not None
                    batch_candidates[effect.id] = _prepare_domain_batch(
                        compiler,
                        domain_calls[effect.id],
                        bound_points,
                        candidate_batch,
                        legal_cut_offsets=candidate_cuts,
                        batch_ordinal=next_batch_ordinals[effect.id],
                        inspection_requested=inspection_requested,
                        inspection_query=inspection_query,
                        max_retained_bytes=max_candidate_retained_bytes,
                    )
            compatible_sizes = tuple(
                candidate.compatible_point_count
                for candidate in batch_candidates.values()
            )
            if not compatible_sizes:
                raise AssertionError("domain coverage produced no compatible prefix")
            coverage_batch = candidate_batch[: min(compatible_sizes)]
        else:
            coverage_batch = candidate_batch
        next_domain_max_points: list[int] = []
        local_effects = _materialize_local_coverage(
            bound_points,
            target=local_target,
            point_ordinals=coverage_batch,
            initial_probe=(
                initial_local_probe
                if initial_local_probe is not None
                and (covered_point_count == 0 or initial_local_probe.point_invariant)
                else None
            ),
        )
        if local_effects is not None:
            yield _MaterializedLocalCoverage(local_effects)
        regions = _stable_host_regions(coverage_batch, local_effects)
        initial_frame = _static_state_frame(local_effects, coverage_batch[0])
        scheduled_local_effects = _coalesce_host_state(
            local_effects,
            regions,
            suppress_initial_anchor=(
                has_previous_static_frame
                and initial_frame is not None
                and initial_frame == previous_static_frame
            ),
        )
        selected_compute = (
            ()
            if scheduled_local_effects is None
            else scheduled_local_effects.compute_operations
        )
        selected_effects = (
            ()
            if scheduled_local_effects is None
            else scheduled_local_effects.effect_operations
        )
        batch_region_point_count = 0
        for region in regions:
            yield from (
                effect for effect in selected_compute if effect.point_index in region
            )
            for effect_index, effect in enumerate(effects):
                if scheduled_local_effects is not None:
                    yield from (
                        item
                        for item in selected_effects[effect_index]
                        if item.point_index in region
                    )
                if isinstance(effect, LogicalDomainExecution):
                    assert compiler is not None
                    batch_ordinal = next_batch_ordinals[effect.id]
                    job = _compile_domain_batch(
                        batch_candidates[effect.id],
                        domain_calls[effect.id],
                        bound_points,
                        region,
                        legal_cut_offsets=tuple(range(1, len(region) + 1)),
                        batch_ordinal=batch_ordinal,
                        inspection_requested=inspection_requested,
                        inspection_query=inspection_query,
                    )
                    next_domain_max_points.append(job.execution.next_batch_max_points)
                    yield job
                    next_batch_ordinals[effect.id] = batch_ordinal + 1
            batch_region_point_count += len(region)
            region_end = covered_point_count + batch_region_point_count
            for group in _point_group_checkpoints_between(
                group_checkpoints,
                start=region_end - len(region),
                end=region_end,
            ):
                yield RunCoverageCheckpoint(group.id, group.ordinals)
        previous_static_frame = _static_state_frame(
            local_effects,
            coverage_batch[-1],
        )
        has_previous_static_frame = previous_static_frame is not None
        covered_point_count += len(coverage_batch)
        remaining = total_point_count - covered_point_count
        if not remaining:
            return
        if has_domain_calls:
            if not next_domain_max_points:
                raise AssertionError("domain coverage produced no continuation maximum")
            assert compiler is not None
            next_batch_max_points = min(remaining, *next_domain_max_points)
        else:
            next_batch_max_points = min(remaining, _MAX_LOCAL_COVERAGE_BATCH_SIZE)


def _materialize_local_coverage(
    bound_points: MaterializedBoundPoints,
    *,
    target: LocalTargetPlan | None,
    point_ordinals: tuple[int, ...],
    initial_probe: _InitialLocalProbe | None,
) -> MaterializedLocalEffects | None:
    if target is None:
        return None
    if initial_probe is not None and initial_probe.point_invariant:
        return _retarget_invariant_local_probe(
            initial_probe,
            bound_points,
            point_ordinals,
        )
    if initial_probe is None:
        return materialize_local_execution(
            bound_points,
            target=target,
            point_ordinals=point_ordinals,
        )
    if point_ordinals[0] != initial_probe.ordinal:
        raise AssertionError("initial local probe must cover the first execution point")
    remaining_ordinals = point_ordinals[1:]
    if not remaining_ordinals:
        return initial_probe.effects
    remaining = materialize_local_execution(
        bound_points,
        target=target,
        point_ordinals=remaining_ordinals,
    )
    return MaterializedLocalEffects(
        compute_operations=(
            *initial_probe.effects.compute_operations,
            *remaining.compute_operations,
        ),
        effect_operations=tuple(
            (*initial, *rest)
            for initial, rest in zip(
                initial_probe.effects.effect_operations,
                remaining.effect_operations,
                strict=True,
            )
        ),
    )


def _retarget_invariant_local_probe(
    probe: _InitialLocalProbe,
    bound_points: MaterializedBoundPoints,
    point_ordinals: tuple[int, ...],
) -> MaterializedLocalEffects:
    if probe.effects.compute_operations:
        raise AssertionError("point-invariant local coverage cannot contain compute")
    source_uid = probe.point_uid

    def retarget(
        covered: RunCoverageEffect,
        ordinal: int,
    ) -> RunCoverageEffect:
        operation = covered.operation
        if not isinstance(operation, ApplyStateOperation):
            raise AssertionError(
                "point-invariant local coverage must contain only state operations"
            )
        if not operation.operation_id.startswith(source_uid):
            raise AssertionError("initial local probe operation id has another point")
        point_uid = bound_points.point_domain.points[ordinal].logical_id.value
        return RunCoverageEffect(
            point_index=ordinal,
            operation=replace(
                operation,
                operation_id=point_uid + operation.operation_id[len(source_uid) :],
            ),
        )

    return MaterializedLocalEffects(
        compute_operations=(),
        effect_operations=tuple(
            tuple(
                retarget(covered, ordinal)
                for ordinal in point_ordinals
                for covered in group
            )
            for group in probe.effects.effect_operations
        ),
    )


def _prepare_domain_batch(
    compiler: DomainCompiler,
    call: DomainCallView,
    bound_points: MaterializedBoundPoints,
    point_ordinals: tuple[int, ...],
    *,
    legal_cut_offsets: tuple[int, ...],
    batch_ordinal: int,
    inspection_requested: bool,
    inspection_query: CompiledProgramInspectionQuery | None,
    max_retained_bytes: int,
) -> DomainBatchCandidate:
    request = make_domain_batch_request(
        call,
        bound_points,
        point_ordinals,
        legal_cut_offsets=legal_cut_offsets,
        batch_ordinal=batch_ordinal,
        inspection_requested=inspection_requested,
        inspection_query=inspection_query,
    )
    candidate_value = cast("object", compiler.prepare_batch(request))
    if not isinstance(candidate_value, DomainBatchCandidate):
        raise TypeError(
            "domain compiler prepare_batch must return DomainBatchCandidate"
        )
    candidate = candidate_value
    size = candidate.compatible_point_count
    if (
        type(size) is not int
        or not 1 <= size <= len(point_ordinals)
        or size not in legal_cut_offsets
    ):
        raise ValueError(
            "domain batch candidate compatible point count must be a positive "
            "candidate prefix ending at a legal point cut"
        )
    preparation_cost = cast("object", candidate.preparation_cost)
    if not isinstance(preparation_cost, DomainBatchPreparationCost):
        raise TypeError(
            "domain batch candidate preparation_cost must be DomainBatchPreparationCost"
        )
    analyzed = preparation_cost.analyzed_point_count
    retained = preparation_cost.retained_bytes
    if (
        type(analyzed) is not int
        or not size <= analyzed <= len(point_ordinals)
        or type(retained) is not int
        or not 0 <= retained <= max_retained_bytes
    ):
        raise ValueError(
            "domain batch candidate preparation cost must cover its compatible "
            "prefix and remain within the retained-byte limit"
        )
    return candidate


def _domain_batch_preparation_limits(
    compiler: DomainCompiler,
    point_count: int,
) -> DomainBatchPreparationLimits:
    limits_value = cast(
        "object",
        compiler.initial_batch_preparation_limits(point_count),
    )
    if not isinstance(limits_value, DomainBatchPreparationLimits):
        raise TypeError(
            "domain compiler initial_batch_preparation_limits must return "
            "DomainBatchPreparationLimits"
        )
    if (
        type(limits_value.max_points) is not int
        or not 1 <= limits_value.max_points <= point_count
        or type(limits_value.max_retained_bytes) is not int
        or limits_value.max_retained_bytes < 1
    ):
        raise ValueError(
            "domain compiler preparation limits must have a positive covered "
            "point count and retained-byte budget"
        )
    return limits_value


def _point_group_checkpoint_offsets(
    groups: Sequence[PointExecutionGroup],
) -> dict[int, PointExecutionGroup]:
    checkpoints: dict[int, PointExecutionGroup] = {}
    point_count = 0
    for group in groups:
        point_count += len(group.ordinals)
        checkpoints[point_count] = group
    return checkpoints


def _preferred_candidate_point_count(
    checkpoints: dict[int, PointExecutionGroup],
    *,
    start: int,
    remaining: int,
    max_points: int,
) -> int:
    """Prefer a group end without making it a physical batching constraint."""

    limit = min(remaining, max_points)
    preferred_ends = tuple(
        offset for offset in checkpoints if start < offset <= start + limit
    )
    return limit if not preferred_ends else max(preferred_ends) - start


def _point_group_checkpoints_between(
    checkpoints: dict[int, PointExecutionGroup],
    *,
    start: int,
    end: int,
) -> tuple[PointExecutionGroup, ...]:
    return tuple(
        group for offset, group in checkpoints.items() if start < offset <= end
    )


def _stable_host_regions(
    region: tuple[int, ...],
    local_effects: MaterializedLocalEffects | None,
) -> tuple[tuple[int, ...], ...]:
    """Group adjacent points that require the same static host state frame.

    Pure host computation does not delimit a real-time batch. Static state is
    idempotent and can be reconciled once at the start of a stable region.
    Invocations, acquisitions, and state backed by point-local payloads remain
    hard point boundaries because repeating or reordering them changes meaning.
    """

    selected: list[tuple[int, ...]] = []
    start = 0
    previous_frame: tuple[tuple[ApplyStateOperation, ...], ...] | None = None
    for offset, ordinal in enumerate(region):
        frame = _static_state_frame(local_effects, ordinal)
        changed = frame is None or previous_frame is None or frame != previous_frame
        if offset and changed:
            selected.append(region[start:offset])
            start = offset
        previous_frame = frame
    selected.append(region[start:])
    return tuple(selected)


def _static_state_frame(
    local_effects: MaterializedLocalEffects | None,
    point_index: int,
) -> tuple[tuple[ApplyStateOperation, ...], ...] | None:
    """Return the comparable state frame for one point, or a hard boundary."""

    if local_effects is None:
        return ()
    frame: list[tuple[ApplyStateOperation, ...]] = []
    for group in local_effects.effect_operations:
        operations: list[ApplyStateOperation] = []
        for covered in group:
            if covered.point_index != point_index:
                continue
            operation = covered.operation
            if not isinstance(operation, ApplyStateOperation):
                return None
            if any(
                isinstance(target.value.root, PayloadRef)
                for target in operation.targets
            ):
                return None
            operations.append(replace(operation, operation_id=""))
        frame.append(tuple(operations))
    return tuple(frame)


def _coalesce_host_state(
    local_effects: MaterializedLocalEffects | None,
    regions: tuple[tuple[int, ...], ...],
    *,
    suppress_initial_anchor: bool = False,
) -> MaterializedLocalEffects | None:
    """Keep one materialized static-state frame at each stable region anchor."""

    if local_effects is None:
        return None
    anchors = {region[0] for region in regions}
    if suppress_initial_anchor:
        anchors.remove(regions[0][0])
    return MaterializedLocalEffects(
        compute_operations=local_effects.compute_operations,
        effect_operations=tuple(
            tuple(effect for effect in group if effect.point_index in anchors)
            for group in local_effects.effect_operations
        ),
    )


def _compile_domain_batch(
    candidate: DomainBatchCandidate,
    call: DomainCallView,
    bound_points: MaterializedBoundPoints,
    point_ordinals: tuple[int, ...],
    *,
    legal_cut_offsets: tuple[int, ...],
    batch_ordinal: int,
    inspection_requested: bool,
    inspection_query: CompiledProgramInspectionQuery | None,
) -> RunDomainJob:
    request = make_domain_batch_request(
        call,
        bound_points,
        point_ordinals,
        legal_cut_offsets=legal_cut_offsets,
        batch_ordinal=batch_ordinal,
        inspection_requested=inspection_requested,
        inspection_query=inspection_query,
    )
    execution_candidate = cast(
        "object",
        candidate.compile(request),
    )
    if not isinstance(execution_candidate, PreparedDomainExecution):
        raise TypeError(
            "domain batch candidate compile must return PreparedDomainExecution"
        )
    return RunDomainJob(
        id=f"{call.id}:batch-{batch_ordinal}",
        point_ordinals=point_ordinals,
        execution=execution_candidate,
    )


def _implementation_problems(
    *,
    has_domain_call: bool,
    has_domain_compiler: bool,
    local_instrument_required: bool,
    domain_instrument_required: bool,
    has_local_instrument_catalog: bool,
) -> tuple[Problem, ...]:
    """Report missing effect/dataflow implementations directly from typed edges."""

    problems: list[Problem] = []
    if has_domain_call and not has_domain_compiler:
        problems.append(
            _planning_problem(
                "domain_compiler_missing",
                "the typed domain call has no configured compiler",
            )
        )
    if local_instrument_required and not has_local_instrument_catalog:
        problems.append(
            _planning_problem(
                "local_instrument_catalog_missing",
                "local effects or products require an instrument contract catalog",
            )
        )
    if domain_instrument_required and not has_local_instrument_catalog:
        problems.append(
            _planning_problem(
                "domain_instrument_catalog_missing",
                "domain execution instruments require an instrument contract catalog",
            )
        )
    return tuple(problems)


def _validate_entity_acquisition_cohort_placement(
    cohorts: Sequence[EntityAcquisitionCohortPlan],
    *,
    local_product_use_ids: frozenset[ProductUseId],
) -> None:
    problems: list[Problem] = []
    for cohort in cohorts:
        cohort_use_ids = frozenset(
            use_id for member in cohort.members for use_id in member.product_use_ids
        )
        unsupported = cohort_use_ids - local_product_use_ids
        if not unsupported:
            continue
        problems.append(
            _planning_problem(
                "entity_acquisition_cohort_placement_unsupported",
                f"entity acquisition cohort {cohort.id!r} must be owned by "
                "local hardware acquisition",
                details={
                    "cohort_id": cohort.id,
                    "unsupported_product_use_ids": sorted(
                        use_id.value for use_id in unsupported
                    ),
                },
            )
        )
    if problems:
        raise CheckFailed(problems)


def _planning_problem(
    code: str,
    message: str,
    *,
    details: dict[str, object] | None = None,
) -> Problem:
    return problem(
        code,
        message,
        phase=ProblemPhase.PLANNING,
        location=model_location("experiment_system"),
        details=details or {},
    )
