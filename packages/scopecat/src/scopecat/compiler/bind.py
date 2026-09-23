"""Bind one verified logical program to an accepted configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from scopecat.compiler.bound_facts import BoundProgramFacts
from scopecat.compiler.bound_specialization import specialize_bound_facts
from scopecat.compiler.bound_verification import (
    ProgramRelationConsumer,
    bound_relation_consumers,
    verify_bound_facts,
)
from scopecat.compiler.diagnostics import compiler_problem
from scopecat.compiler.environment import ConfigEnvironment
from scopecat.compiler.frontend.binding_lowering import (
    build_resource_requirements,
)
from scopecat.compiler.frontend.logical_lowering import (
    input_row,
    lower_parameter_overlay_intent,
    lower_point_domain,
    validate_entity_inputs,
)
from scopecat.compiler.frontend.logical_verification import VerifiedLogicalProgram
from scopecat.compiler.frontend.measurement_compute_lowering import (
    lower_measurement_compute_graph,
)
from scopecat.compiler.frontend.parameter_contract_validation import (
    validate_parameter_contracts,
)
from scopecat.compiler.frontend.problems import raise_frontend_problem
from scopecat.compiler.frontend.product_lowering import lower_products
from scopecat.compiler.frontend.static_evaluation import StaticRelationEvaluator
from scopecat.compiler.point_domain import VerifiedPointDomain
from scopecat.compiler.relations.context import ParameterRelationData
from scopecat.compiler.relations.evaluation import (
    normalize_relation_parameter_import,
)
from scopecat.compiler.relations.parameter_reads import ParameterReadRecorder
from scopecat.compiler.relations.verification import (
    ExpressionImportNamespace,
    ExpressionTypeBindings,
    ExpressionVerificationError,
    RowType,
    scalar_expression_imports,
)
from scopecat.compiler.topology_selection import (
    TopologyConnectionSetResolution,
    TopologyEntitySetResolution,
    TopologySelectionError,
    TopologyTableResolution,
    resolve_topology_connection_set,
    resolve_topology_entity_set,
)
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.frozen import freeze_json_mapping
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.problems import Problem, ProblemPhase, model_location
from scopecat.kernel.value_types import Scalar, Table
from scopecat.kernel.value_validation import ValueValidationError
from scopecat.measurements.records import BoundRecordUse, ValueRecordUse
from scopecat.program.logical import LogicalProgram
from scopecat.program.parameters import (
    ParameterValueContract,
)
from scopecat.program.recording import LogicalValueRecordSelection
from scopecat.program.table_values import (
    TopologyConnectionSetSource,
    TopologyEntitySetSource,
)
from scopecat.records.config import ConfigProfileSnapshot, Topology
from scopecat.records.parameter import ParameterCatalog
from scopecat.records.parameter_read import BindingParameterRead


@dataclass(frozen=True, slots=True)
class BoundDomainTarget:
    """The sole domain target and authority selected from configuration."""

    id: str
    kind: str
    configuration: Mapping[str, object]
    instrument_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BoundPlan:
    """One verified logical program bound to its accepted configuration."""

    program: VerifiedLogicalProgram
    bindings: BoundProgramFacts
    point_domain: VerifiedPointDomain
    environment: ConfigEnvironment
    domain_target: BoundDomainTarget | None


def bind_program(
    program: VerifiedLogicalProgram,
    environment: ConfigEnvironment,
) -> BoundPlan:
    """Lower, specialize, verify, and bind one logical program exactly once."""

    try:
        lowered = _lower_logical_program(
            program,
            environment,
        )
        return _bind_program_facts(
            program,
            lowered,
            environment,
        )
    except ExpressionVerificationError as error:
        raise_frontend_problem(
            f"expression_{error.code}",
            error.reason,
            "expression",
            path=error.path,
            details={
                "relation_code": error.code,
                "expression_path": list(error.path),
            },
        )


def _lower_logical_program(
    verified: VerifiedLogicalProgram,
    environment: ConfigEnvironment,
) -> BoundProgramFacts:
    logical = verified.program
    config = environment.config
    parameter_catalog = config.parameter_catalog
    topology = config.topology
    inputs = logical.inputs
    validate_parameter_contracts(
        parameter_catalog,
        logical.parameter_contracts,
    )
    validate_entity_inputs(topology, logical.entity_inputs, inputs)
    root_type_bindings = _relation_type_bindings(logical, parameter_catalog)
    point_domain = lower_point_domain(
        logical.point_domain,
        inputs=inputs,
        type_bindings=root_type_bindings,
        layout=logical.point_domain_layout,
    )
    type_bindings = replace(
        root_type_bindings,
        point_row=RowType.from_table(point_domain.value_type),
    )
    resource_requirements = build_resource_requirements(
        topology,
        logical.resource_ports,
        inputs=inputs,
        type_bindings=type_bindings,
    )
    topology_entity_sets = _resolve_topology_entity_sets(logical, topology)
    recorder = ParameterReadRecorder()
    static_evaluator = StaticRelationEvaluator(
        environment.parameters,
        {resolution.source: resolution for resolution in topology_entity_sets.values()},
        parameter_reads=recorder,
    )
    products = lower_products(
        static_evaluator,
        topology,
        logical.product_record_selections,
        verified.product_declarations,
        inputs,
        type_bindings=type_bindings,
        input_row=input_row,
    )
    record_product_uses = products.product_uses
    measurement_computes = lower_measurement_compute_graph(
        verified,
        record_product_uses,
    )
    product_uses = (
        *record_product_uses,
        *measurement_computes.input_product_uses,
    )
    uses_by_product = {
        product_id: tuple(
            use.id for use in product_uses if use.product_id == product_id
        )
        for execution in logical.domain_executions
        for _result_id, product_id in execution.results
    }
    value_record_uses = tuple(
        ValueRecordUse(
            id=record.id,
            value_id=record.value_id,
            source_value_id=record.source_value_id,
            value_type=record.value_type,
            requires_execution=record.value_id in verified.operation_results,
            role=record.role,
            metadata=record.metadata,
        )
        for record in logical.value_record_selections
    )
    product_record_group_iterator = iter(products.record_use_groups)
    value_record_iterator = iter(value_record_uses)
    record_uses: list[BoundRecordUse] = []
    for selection in logical.record_selections:
        if isinstance(selection, LogicalValueRecordSelection):
            record_uses.append(next(value_record_iterator))
        else:
            record_uses.extend(next(product_record_group_iterator))
    return BoundProgramFacts(
        point_domain=point_domain,
        topology_entity_sets=topology_entity_sets,
        resource_requirements=tuple(resource_requirements),
        live_compute_ids=frozenset(node.id for node in logical.compute_nodes),
        domain_result_use_ids={
            (execution.id, result_id): uses_by_product.get(product_id, ())
            for execution in logical.domain_executions
            for result_id, product_id in execution.results
        },
        measurement_computes=measurement_computes.computes,
        parameter_overlays=tuple(
            lower_parameter_overlay_intent(
                parameter_catalog,
                static_evaluator,
                intent,
                inputs,
                type_bindings=type_bindings,
            )
            for intent in logical.parameter_overlays
        ),
        product_defs=products.product_defs,
        product_uses=product_uses,
        record_uses=tuple(record_uses),
        parameter_reads=(
            BindingParameterRead(phase="frontend", evidence=recorder.snapshot()),
        ),
    )


def _resolve_topology_entity_sets(
    program: LogicalProgram,
    topology: Topology,
) -> dict[ValueId, TopologyTableResolution]:
    selected: dict[ValueId, TopologyTableResolution] = {}
    for definition in program.value_defs:
        source = definition.source
        if not isinstance(
            source, TopologyEntitySetSource | TopologyConnectionSetSource
        ):
            continue
        if not isinstance(definition.value_type, Table):
            raise AssertionError("topology selection source must own a table value")
        try:
            resolution: TopologyEntitySetResolution | TopologyConnectionSetResolution
            if isinstance(source, TopologyEntitySetSource):
                resolution = resolve_topology_entity_set(
                    topology,
                    source,
                    definition.value_type,
                )
            else:
                resolution = resolve_topology_connection_set(
                    topology,
                    source,
                    definition.value_type,
                )
            selected[definition.id] = resolution
        except TopologySelectionError as error:
            details = (
                {
                    "entity_kind": source.entity_kind,
                    "count": source.count,
                    "connected": source.connected,
                    "anchor_id": source.anchor_id,
                    "connection_kind": source.connection_kind,
                }
                if isinstance(source, TopologyEntitySetSource)
                else {
                    "endpoint_entity_kind": source.endpoint_entity_kind,
                    "connection_entity_kind": source.connection_entity_kind,
                    "connection_kind": source.connection_kind,
                    "matching": source.matching,
                }
            )
            raise_frontend_problem(
                "topology_selection_failed",
                str(error),
                "logical_program",
                path=("values", definition.id.qualified_name),
                details=details,
            )
    return selected


def _relation_type_bindings(
    program: LogicalProgram,
    parameter_catalog: ParameterCatalog,
) -> ExpressionTypeBindings:
    """Project logical contracts into the expression type environment."""

    parameter_types: dict[str, Scalar] = {}
    for contract in program.parameter_contracts:
        if not isinstance(contract, ParameterValueContract):
            continue
        definition = parameter_catalog.get(contract.parameter_id)
        value_type = (
            definition.value_type if definition is not None else contract.value_type
        )
        if isinstance(value_type, Scalar):
            parameter_types[contract.parameter_id] = value_type
    return ExpressionTypeBindings(
        inputs={
            port.id: port.value_type
            for port in program.input_ports
            if isinstance(port.value_type, Scalar)
        },
        parameters=parameter_types,
    )


def _bind_program_facts(
    program: VerifiedLogicalProgram,
    bindings: BoundProgramFacts,
    environment: ConfigEnvironment,
) -> BoundPlan:
    """Specialize and verify facts introduced by configuration binding."""

    specialized = specialize_bound_facts(
        program,
        bindings,
        parameters=environment.parameters,
    )
    point_domain = verify_bound_facts(
        program,
        specialized,
        program_id=program.experiment_id,
        phase=ProblemPhase.PLANNING,
    )
    problems = list(
        _relation_import_problems(
            program,
            specialized,
            point_domain,
            environment.parameters,
        )
    )
    if problems:
        raise CheckFailed(problems)
    return _make_bound_plan(
        program,
        specialized,
        point_domain,
        environment,
    )


def _make_bound_plan(
    program: VerifiedLogicalProgram,
    bindings: BoundProgramFacts,
    point_domain: VerifiedPointDomain,
    environment: ConfigEnvironment,
) -> BoundPlan:
    """Attach shared config-bound facts to their accepted environment."""

    return BoundPlan(
        program=program,
        bindings=bindings,
        point_domain=point_domain,
        environment=environment,
        domain_target=_bind_domain_target(
            program,
            environment.config,
        ),
    )


def _bind_domain_target(
    program: VerifiedLogicalProgram,
    config: ConfigProfileSnapshot,
) -> BoundDomainTarget | None:
    """Select the one configured target for every domain call in the program."""

    if not program.program.domain_executions:
        return None
    target = config.domain_target
    if target is None:
        raise CheckFailed(
            (
                compiler_problem(
                    "domain_target_missing",
                    "the accepted system configuration has no domain target",
                    model_location("config", "system", "domain_target"),
                    phase=ProblemPhase.PLANNING,
                ),
            )
        )
    return BoundDomainTarget(
        id=target.id,
        kind=target.kind,
        configuration=freeze_json_mapping(
            target.configuration,
            path=f"domain target {target.id!r} configuration",
        ),
        instrument_ids=tuple(target.instrument_ids),
    )


def _relation_import_problems(
    logical: VerifiedLogicalProgram,
    program: BoundProgramFacts,
    point_domain: VerifiedPointDomain,
    parameters: ParameterRelationData,
) -> tuple[Problem, ...]:
    problems: list[Problem] = []
    for consumer in bound_relation_consumers(logical, program, point_domain):
        plan = consumer.plan
        for imported in scalar_expression_imports(plan):
            if imported.namespace is ExpressionImportNamespace.INPUT:
                problems.append(_unresolved_input_problem(consumer, imported.id))
                continue
            try:
                normalize_relation_parameter_import(
                    plan,
                    imported,
                    parameters,
                )
            except ValueValidationError as error:
                problems.append(_parameter_import_problem(consumer, error))
    return tuple(problems)


def _unresolved_input_problem(
    consumer: ProgramRelationConsumer,
    input_id: str,
) -> Problem:
    return compiler_problem(
        "bound_input_unresolved",
        f"bound relation still depends on unresolved input {input_id!r}",
        model_location(
            consumer.location.root,
            *consumer.location.path,
            "inputs",
            input_id,
        ),
        phase=ProblemPhase.PLANNING,
        details={
            "consumer_kind": consumer.kind.value,
            "input_id": input_id,
        },
    )


def _parameter_import_problem(
    consumer: ProgramRelationConsumer,
    error: ValueValidationError,
) -> Problem:
    missing = error.code == "unknown_parameter"
    parameter_id = (
        error.path[1]
        if len(error.path) > 1 and isinstance(error.path[1], str)
        else None
    )
    return compiler_problem(
        "bound_parameter_missing" if missing else "bound_parameter_contract_mismatch",
        (
            "accepted configuration cannot satisfy relation "
            f"parameter import: {error.reason}"
        ),
        model_location(
            consumer.location.root,
            *consumer.location.path,
            *error.path,
        ),
        phase=ProblemPhase.PLANNING,
        details={
            "consumer_kind": consumer.kind.value,
            **({"parameter_id": parameter_id} if parameter_id is not None else {}),
            "value_path": list(error.path),
        },
    )
