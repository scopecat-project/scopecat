"""Materialize the bound symbolic point space for physical planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast, overload, override

from scopecat.compiler.bind import BoundPlan
from scopecat.compiler.diagnostics import compiler_problem
from scopecat.compiler.entity_resolution import (
    EntityResolutionError,
    resolve_entity,
)
from scopecat.compiler.environment import ConfigEnvironment
from scopecat.compiler.parameter_overlays import resolve_point_parameters
from scopecat.compiler.point_domain import (
    MaterializedPoint,
    MaterializedPointDomain,
    PointDomainEvaluationError,
    prepare_point_domain,
)
from scopecat.compiler.relations.context import EvalContext, ParameterRelationData
from scopecat.compiler.relations.evaluation import (
    evaluate_scalar,
    evaluate_table_value,
)
from scopecat.compiler.relations.parameter_reads import ParameterReadRecorder
from scopecat.compiler.value_resolution import BoundValueResolver, ProgramValue
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.point_identity import LogicalPointId, PointDomainId
from scopecat.kernel.points import PointProposalAttempt
from scopecat.kernel.problems import (
    Problem,
    ProblemPhase,
    model_location,
)
from scopecat.kernel.value_data import Row
from scopecat.kernel.value_types import Scalar, Table, ValueType
from scopecat.kernel.value_validation import ValueValidationError, coerce_literal
from scopecat.program.expressions import ArrayExpr, LiteralArrayExpr, ScalarExpr
from scopecat.program.logical import LogicalDomainExecution
from scopecat.records.parameter_read import DomainInputParameterRead


@dataclass(frozen=True, slots=True)
class MaterializedBoundPoints:
    """One bound plan with random-access point and parameter views."""

    bound_plan: BoundPlan
    point_domain: MaterializedPointDomain
    point_parameters: Sequence[ParameterRelationData]

    def __post_init__(self) -> None:
        if len(self.point_parameters) != len(self.point_domain.points):
            msg = "materialized points and parameter bindings must have equal length"
            raise ValueError(msg)

    def bind_domain_inputs(
        self,
        execution_id: str,
        input_kind: Literal["program", "compiler"],
        input_ids: Sequence[str],
        ordinals: Sequence[int],
        *,
        parameter_reads: list[DomainInputParameterRead] | None = None,
    ) -> tuple[tuple[str, tuple[object, ...]], ...]:
        """Evaluate selected domain inputs for selected logical ordinals."""

        selected_input_ids = tuple(input_ids)
        selected = tuple(ordinals)
        execution = next(
            item
            for item in self.bound_plan.program.program.domain_executions
            if item.id == execution_id
        )
        available_inputs = dict(
            execution.inputs if input_kind == "program" else execution.compiler_inputs
        )
        known_input_ids = tuple(available_inputs)
        selected_input_set = set(selected_input_ids)
        if not selected_input_ids:
            return ()
        if selected_input_ids != tuple(
            input_id for input_id in known_input_ids if input_id in selected_input_set
        ):
            raise ValueError(
                "domain input binding must select known inputs in typed order"
            )
        problems: list[Problem] = []
        columns: dict[str, list[object]] = {
            input_id: [] for input_id in selected_input_ids
        }
        for ordinal in selected:
            point = self.point_domain.points[ordinal]
            parameters = self.point_parameters[ordinal]
            input_values = _domain_inputs(
                execution,
                BoundValueResolver(
                    self.bound_plan.program,
                    self.bound_plan.bindings,
                ),
                input_kind,
                point,
                selected_input_ids,
                parameters=parameters,
                problems=problems,
                parameter_reads=parameter_reads,
            )
            if input_values is not None:
                for input_id, value in input_values:
                    columns[input_id].append(value)
        if bool(problems):
            raise CheckFailed(problems)
        return tuple(
            (input_id, tuple(columns[input_id])) for input_id in selected_input_ids
        )


def prepare_bound_points(bound: BoundPlan) -> MaterializedBoundPoints:
    """Prepare point-local evaluation without scanning the complete domain."""

    try:
        point_domain = _prepare_bound_point_domain(bound)
    except ValueValidationError as error:
        raise CheckFailed(
            (
                compiler_problem(
                    "module_point_value_type_mismatch",
                    str(error),
                    model_location("points"),
                    phase=ProblemPhase.PLANNING,
                ),
            )
        ) from error
    return MaterializedBoundPoints(
        bound_plan=bound,
        point_domain=point_domain,
        point_parameters=_PointParameterSequence(
            bound,
            point_domain.points,
        ),
    )


def prepare_candidate_bound_points(
    prepared: MaterializedBoundPoints,
    candidate: PointProposalAttempt,
) -> tuple[PointProposalAttempt, MaterializedBoundPoints]:
    """Resolve and bind one unaccepted coordinate row in an isolated domain."""

    bound = prepared.bound_plan
    problems: list[Problem] = []
    normalized = _normalize_point_domain_row(
        dict(candidate.coordinates),
        entity_columns=bound.point_domain.entity_columns,
        environment=bound.environment,
        problems=problems,
    )
    if problems:
        raise CheckFailed(problems)
    try:
        rows = cast(
            "tuple[Row, ...]",
            coerce_literal(
                bound.point_domain.value_type,
                (normalized,),
                path=("inspection", "point"),
            ),
        )
    except ValueValidationError as error:
        raise CheckFailed(
            (
                compiler_problem(
                    "inspection_point_value_type_mismatch",
                    str(error),
                    model_location("inspection", "point"),
                    phase=ProblemPhase.PLANNING,
                ),
            )
        ) from error
    [row] = rows
    resolved = PointProposalAttempt(
        coordinates=row,
        source=candidate.source,
        region_id=candidate.region_id,
        domain_proposal_fingerprint=candidate.domain_proposal_fingerprint,
        based_on_region_revision=candidate.based_on_region_revision,
    )
    fingerprint = resolved.coordinate_fingerprint.removeprefix("sha256:")
    domain_id = PointDomainId(
        program_id=bound.point_domain.id.program_id,
        domain_id=f"{bound.point_domain.id.domain_id}.inspection-{fingerprint[:16]}",
    )
    point = MaterializedPoint(
        logical_id=LogicalPointId(domain_id, 0),
        row=row,
    )
    point_domain = MaterializedPointDomain(
        id=domain_id,
        points=(point,),
        layout="point_cloud",
    )
    return (
        resolved,
        MaterializedBoundPoints(
            bound_plan=bound,
            point_domain=point_domain,
            point_parameters=_PointParameterSequence(bound, point_domain.points),
        ),
    )


def append_candidate_bound_point(
    prepared: MaterializedBoundPoints,
    candidate: PointProposalAttempt,
) -> tuple[PointProposalAttempt, MaterializedBoundPoints]:
    """Resolve and append one candidate to the canonical run point domain."""

    resolved, isolated = prepare_candidate_bound_points(prepared, candidate)
    ordinal = len(prepared.point_domain.points)
    point = MaterializedPoint(
        logical_id=LogicalPointId(prepared.point_domain.id, ordinal),
        row=isolated.point_domain.points[0].row,
    )
    point_domain = MaterializedPointDomain(
        id=prepared.point_domain.id,
        points=(*prepared.point_domain.points, point),
        layout="point_cloud",
    )
    return (
        resolved,
        MaterializedBoundPoints(
            bound_plan=prepared.bound_plan,
            point_domain=point_domain,
            point_parameters=_PointParameterSequence(
                prepared.bound_plan,
                point_domain.points,
            ),
        ),
    )


def append_candidate_bound_points(
    prepared: MaterializedBoundPoints,
    candidates: Sequence[PointProposalAttempt],
) -> tuple[tuple[PointProposalAttempt, ...], MaterializedBoundPoints]:
    """Resolve and append one complete proposal fragment atomically."""

    resolved_rows = tuple(
        prepare_candidate_bound_points(prepared, candidate)[0]
        for candidate in candidates
    )
    start = len(prepared.point_domain.points)
    appended = tuple(
        MaterializedPoint(
            logical_id=LogicalPointId(prepared.point_domain.id, start + index),
            row=dict(candidate.coordinates),
        )
        for index, candidate in enumerate(resolved_rows)
    )
    point_domain = MaterializedPointDomain(
        id=prepared.point_domain.id,
        points=(*prepared.point_domain.points, *appended),
        layout="point_cloud",
    )
    return (
        resolved_rows,
        MaterializedBoundPoints(
            bound_plan=prepared.bound_plan,
            point_domain=point_domain,
            point_parameters=_PointParameterSequence(
                prepared.bound_plan,
                point_domain.points,
            ),
        ),
    )


class _PointParameterSequence(Sequence[ParameterRelationData]):
    __slots__ = ("_bound", "_points")

    def __init__(
        self,
        bound: BoundPlan,
        points: Sequence[MaterializedPoint],
    ) -> None:
        self._bound = bound
        self._points = points

    @override
    def __len__(self) -> int:
        return len(self._points)

    @overload
    def __getitem__(self, index: int) -> ParameterRelationData: ...

    @overload
    def __getitem__(
        self,
        index: slice,
    ) -> tuple[ParameterRelationData, ...]: ...

    @override
    def __getitem__(
        self,
        index: int | slice,
    ) -> ParameterRelationData | tuple[ParameterRelationData, ...]:
        if isinstance(index, slice):
            return tuple(self[ordinal] for ordinal in range(*index.indices(len(self))))
        point = self._points[index]
        return resolve_point_parameters(
            self._bound.environment.parameters,
            self._bound.bindings.parameter_overlays,
            point_row=point.row,
        )


def _prepare_bound_point_domain(
    bound: BoundPlan,
) -> MaterializedPointDomain:
    entity_columns = bound.point_domain.entity_columns

    def normalize(row: Row) -> Row:
        problems: list[Problem] = []
        selected = _normalize_point_domain_row(
            row,
            entity_columns=entity_columns,
            environment=bound.environment,
            problems=problems,
        )
        if problems:
            raise CheckFailed(problems)
        return selected

    try:
        return prepare_point_domain(
            bound.point_domain,
            bound.environment.parameters,
            row_normalizer=normalize,
        )
    except PointDomainEvaluationError as error:
        raise CheckFailed(
            (
                compiler_problem(
                    "experiment_points_evaluation_failed",
                    f"experiment point domain failed: {error.error}",
                    model_location("point_domain", *error.path),
                    phase=ProblemPhase.PLANNING,
                ),
            )
        ) from error


def _domain_inputs(
    execution: LogicalDomainExecution,
    values: Mapping[ValueId, ProgramValue],
    input_kind: Literal["program", "compiler"],
    point: MaterializedPoint,
    input_ids: tuple[str, ...],
    *,
    parameters: ParameterRelationData,
    problems: list[Problem],
    parameter_reads: list[DomainInputParameterRead] | None = None,
) -> tuple[tuple[str, object], ...] | None:
    input_values: list[tuple[str, object]] = []
    failed = False
    for input_name in input_ids:
        success, value = _materialize_domain_execution_input(
            execution,
            values,
            input_kind=input_kind,
            input_name=input_name,
            point=point,
            parameters=parameters,
            problems=problems,
            parameter_reads=parameter_reads,
        )
        if not success:
            failed = True
            continue
        input_values.append((input_name, value))
    if failed:
        return None
    return tuple(input_values)


def _materialize_domain_execution_input(
    execution: LogicalDomainExecution,
    values: Mapping[ValueId, ProgramValue],
    *,
    input_kind: Literal["program", "compiler"],
    input_name: str,
    point: MaterializedPoint,
    parameters: ParameterRelationData,
    problems: list[Problem],
    parameter_reads: list[DomainInputParameterRead] | None = None,
) -> tuple[bool, object]:
    """Evaluate one selected domain input at one logical point."""

    recorder = ParameterReadRecorder() if parameter_reads is not None else None
    context = EvalContext(
        params=parameters, point_row=point.row, parameter_reads=recorder
    )
    value_ids = dict(
        execution.inputs if input_kind == "program" else execution.compiler_inputs
    )
    input_spec = values[value_ids[input_name]]
    input_ports = (
        execution.program.input_ports
        if input_kind == "program"
        else execution.program.compiler_input_ports
    )
    expected_type = next(
        port.value_type for port in input_ports if port.id == input_name
    )
    try:
        evaluated = _evaluate_domain_input(
            input_spec,
            context=context,
            expected_type=expected_type,
        )
        value = coerce_literal(
            expected_type,
            evaluated,
            path=(
                "domain_executions",
                execution.id,
                "points",
                point.logical_ordinal,
                f"{input_kind}_inputs",
                input_name,
            ),
        )
        if parameter_reads is not None and recorder is not None:
            parameter_reads.append(
                DomainInputParameterRead(
                    point_ordinal=point.logical_ordinal,
                    input_kind=input_kind,
                    input_id=input_name,
                    evidence=recorder.snapshot(),
                )
            )
        return True, _unwrap_domain_input(value)
    except (ArithmeticError, KeyError, TypeError, ValueError) as error:
        problems.append(
            compiler_problem(
                "domain_execution_input_evaluation_failed",
                f"domain execution input {input_name!r} failed for point "
                f"{point.logical_ordinal}: {error}",
                model_location(
                    "domain_executions",
                    execution.id,
                    "points",
                    point.logical_ordinal,
                    "inputs",
                    input_name,
                ),
                phase=ProblemPhase.PLANNING,
            )
        )
        return False, None


def _evaluate_domain_input(
    input_spec: ProgramValue,
    *,
    context: EvalContext,
    expected_type: ValueType,
) -> object:
    if isinstance(input_spec, ScalarExpr):
        return evaluate_scalar(
            input_spec,
            context,
            expected_type=cast("Scalar", expected_type),
        )
    if isinstance(input_spec, LiteralArrayExpr):
        return input_spec.value
    if isinstance(input_spec, ArrayExpr):
        raise AssertionError("domain array input must be bound before planning")
    return evaluate_table_value(
        input_spec,
        cast("Table", expected_type),
        context,
    )


def _unwrap_domain_input(value: object) -> object:
    if isinstance(value, PayloadValue):
        return value.payload
    if isinstance(value, list):
        return [_unwrap_domain_input(item) for item in cast("list[object]", value)]
    if isinstance(value, tuple):
        selected = cast("tuple[object, ...]", value)
        return tuple(_unwrap_domain_input(item) for item in selected)
    if isinstance(value, Mapping):
        return {
            name: _unwrap_domain_input(item)
            for name, item in cast("Mapping[object, object]", value).items()
        }
    return value


def _normalize_point_domain_row(
    row: Row,
    *,
    entity_columns: Sequence[str],
    environment: ConfigEnvironment,
    problems: list[Problem],
) -> Row:
    selected = dict(row)
    for column_id in entity_columns:
        value = selected.get(column_id)
        if value is None:
            continue
        entity = _resolve_entity(value, environment, problems)
        if entity is not None:
            selected[column_id] = entity
    return selected


def _resolve_entity(
    value: object,
    environment: ConfigEnvironment,
    problems: list[Problem],
) -> EntityRef | None:
    selected = value if isinstance(value, EntityRef) else str(value)
    try:
        return resolve_entity(environment.config.topology, selected)
    except EntityResolutionError as error:
        issue = error.issue
    if issue.code == "unknown_entity":
        problems.append(
            compiler_problem(
                "unknown_authoring_entity",
                f"experiment references unknown entity {issue.entity_id}",
                model_location("entity", issue.entity_id),
                phase=ProblemPhase.PLANNING,
            )
        )
        return None
    problems.append(
        compiler_problem(
            "authoring_entity_kind_mismatch",
            f"entity {issue.entity_id} has kind {issue.actual_kind}, "
            f"not {issue.requested_kind}",
            model_location("entity", issue.entity_id),
            phase=ProblemPhase.PLANNING,
        )
    )
    return None
