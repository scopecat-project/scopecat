from __future__ import annotations

import inspect
from pathlib import Path
from typing import Annotated, assert_type, cast

import pytest
import scopecat as sc
from scopecat.compiler.bind import bind_program
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.compiler.topology_selection import TopologyEntitySetResolution
from scopecat.config.documents import load_config_snapshot_document
from scopecat.config.environment import build_config_environment
from scopecat.measurements.records import EntityRecordPlan, plan_records
from scopecat.planning.point_materialization import prepare_bound_points
from scopecat.program.products import RecordSelection
from scopecat.records.config import Topology, TopologyConnection

from scopecat_quantum import authoring
from scopecat_quantum.circuits import Measure
from scopecat_quantum.gates import GateCall, GateParameterKind
from scopecat_quantum.measurement_computes import (
    BinaryIqDiscriminator,
    BinaryIqProbabilityProducts,
    IqCentroid,
    binary_iq_probabilities,
)
from scopecat_quantum.programs import Parallel as QuantumParallel
from scopecat_quantum.programs import ParallelEach as QuantumParallelEach
from scopecat_quantum.programs import estimate_quantum_program_workload

_REPO_ROOT = Path(__file__).parents[3]


def test_program_decorator_infers_ports_identity_description_and_results() -> None:
    x = authoring.single_qubit_gate("x")
    elaborations = 0

    @authoring.program(id="test.quantum.decorated")
    def x_count(
        qubit: authoring.Qubit,
        count: Annotated[int, GateParameterKind.INTEGER],
    ) -> authoring.QuantumFragment:
        """Repeat X and measure once."""

        nonlocal elaborations
        elaborations += 1
        return authoring.sequence(
            authoring.repeat(x(qubit), count),
            authoring.measure(qubit, result="iq_shots"),
        )

    assert elaborations == 1
    assert x_count.id == "test.quantum.decorated"
    assert x_count.description == "Repeat X and measure once."
    assert [port.id for port in x_count.ports] == ["qubit", "count"]
    call = x_count("q0", 2)
    domain = call.domain_call.execution.program
    qubit_type = sc.ScalarType(sc.EntityType(entity_kind="logical_qubit"))
    count_type = sc.ScalarType(sc.IntType(minimum=0))
    assert [port.value_type for port in domain.input_ports] == [
        qubit_type,
        count_type,
    ]
    assert x_count.results[0] is x_count.results["iq_shots"]
    assert x_count.results.iq_shots is x_count.results[0]
    signature = inspect.signature(x_count)
    assert tuple(signature.parameters) == ("qubit", "count")
    assert cast("object", signature.return_annotation) is authoring.QuantumProgramCall
    assert x_count.__wrapped__.__name__ == "x_count"
    assert isinstance(x_count, authoring.ProgramDefinition)


def test_program_decorator_preserves_signature_order_and_rejects_unused_ports() -> None:
    x = authoring.single_qubit_gate("x")

    @authoring.program
    def ordered(
        qubit: authoring.Qubit,
        first: int,
        second: int,
    ) -> authoring.QuantumFragment:
        return authoring.sequence(
            authoring.repeat(x(qubit), second),
            authoring.repeat(x(qubit), first),
            authoring.measure(qubit, result="iq"),
        )

    assert [port.id for port in ordered.ports] == ["qubit", "first", "second"]

    with pytest.raises(ValueError, match="unused scalar ports: 'unused'"):

        @authoring.program
        def invalid(  # pyright: ignore[reportUnusedFunction]
            qubit: authoring.Qubit,
            unused: int,
        ) -> authoring.QuantumFragment:
            return authoring.measure(qubit, result="iq")


def test_definition_signatures_own_every_live_port() -> None:
    x = authoring.single_qubit_gate("x")
    external_count = authoring.scalar_input(
        "external_count",
        GateParameterKind.INTEGER,
    )

    with pytest.raises(ValueError, match="captures undeclared scalar ports"):

        @authoring.program
        def captured_input(  # pyright: ignore[reportUnusedFunction]
            qubit: authoring.Qubit,
        ) -> authoring.QuantumFragment:
            return authoring.sequence(
                authoring.repeat(x(qubit), external_count),
                authoring.measure(qubit, result="iq"),
            )

    fixed = authoring.qubit("fixed")
    with pytest.raises(ValueError, match="captures undeclared formal elements"):

        @authoring.program
        def captured_element(  # pyright: ignore[reportUnusedFunction]
            qubit: authoring.Qubit,
        ) -> authoring.QuantumFragment:
            return authoring.sequence(
                x(qubit),
                x(fixed),
                authoring.measure(qubit, result="iq"),
            )

    external_amplitude = authoring.input(
        "external_amplitude",
        sc.ScalarType(sc.QuantityType(unit="arb")),
    )
    with pytest.raises(ValueError, match="captures undeclared scalar ports"):

        @authoring.pulse_template
        def captured_pulse_input(  # pyright: ignore[reportUnusedFunction]
            qubit: authoring.Qubit,
        ) -> authoring.QuantumFragment:
            return authoring.play(
                authoring.drive(qubit),
                authoring.constant(
                    duration=sc.Quantity(8, "ns"),
                    amplitude=external_amplitude,
                ),
            )


def test_fragment_decorator_expands_from_point_bound_inputs() -> None:
    x = authoring.single_qubit_gate("x")
    y = authoring.single_qubit_gate("y")
    elaborations: list[tuple[int, int]] = []
    envelope = authoring.ProgramFamilyEnvelope(
        allowed_gates=(x, y),
        max_operations=64,
        max_depth=64,
    )

    @authoring.fragment(
        id="test.quantum.seeded-sequence",
        envelope=envelope,
    )
    def seeded_sequence(
        qubit: authoring.Qubit,
        length: Annotated[int, sc.IntType(minimum=1)],
        seed: Annotated[int, sc.IntType(minimum=0)],
    ) -> authoring.QuantumFragment:
        elaborations.append((length, seed))
        return authoring.sequence(
            *(
                x(qubit) if (seed + index) % 2 == 0 else y(qubit)
                for index in range(length)
            )
        )

    @authoring.program(id="test.quantum.seeded-program")
    def seeded_program(
        qubit: authoring.Qubit,
        length: Annotated[int, sc.IntType(minimum=1)],
        seed: Annotated[int, sc.IntType(minimum=0)],
    ) -> authoring.QuantumFragment:
        return authoring.sequence(
            seeded_sequence(qubit, length, seed),
            authoring.measure(qubit, result="iq"),
        )

    assert elaborations == []
    assert seeded_sequence.envelope is envelope
    assert [element.id for element in seeded_sequence.allowed_elements] == ["qubit"]
    assert [definition.id.value for definition in envelope.gate_definitions] == [
        "x",
        "y",
    ]
    assert [port.id for port in seeded_program.ports] == ["qubit", "length", "seed"]
    assert inspect.signature(seeded_sequence) == inspect.signature(
        seeded_sequence.__wrapped__
    )

    bound = authoring.bind(
        seeded_program,
        {"qubit": "q0", "length": 3, "seed": 1},
    )

    assert elaborations == [(3, 1)]
    calls = tuple(
        operation
        for operation in bound.verified.operations
        if isinstance(operation, GateCall)
    )
    assert [call.gate_id.value for call in calls] == ["y", "x", "y"]
    assert [definition.id.value for definition in bound.gate_definitions] == ["x", "y"]
    assert all(
        "fragment[test.quantum.seeded-sequence]" in call.id.value for call in calls
    )


def test_program_family_envelope_rejects_gate_and_size_escapes() -> None:
    x = authoring.single_qubit_gate("x")
    y = authoring.single_qubit_gate("y")

    @authoring.fragment(
        id="test.quantum.bounded-family",
        envelope=authoring.ProgramFamilyEnvelope(
            allowed_gates=(x,),
            max_operations=2,
            max_depth=1,
        ),
    )
    def bounded_family(
        qubit: authoring.Qubit,
        variant: Annotated[int, sc.IntType(minimum=0, maximum=2)],
    ) -> authoring.QuantumFragment:
        if variant == 0:
            return y(qubit)
        if variant == 1:
            return authoring.repeat(x(qubit), 3)
        return authoring.sequence(x(qubit), x(qubit))

    @authoring.program(id="test.quantum.bounded-family-program")
    def declaration(
        qubit: authoring.Qubit,
        variant: Annotated[int, sc.IntType(minimum=0, maximum=2)],
    ) -> authoring.QuantumFragment:
        return authoring.sequence(
            bounded_family(qubit, variant),
            authoring.measure(qubit, result="iq"),
        )

    with pytest.raises(ValueError, match="gates outside its program family envelope"):
        authoring.bind(declaration, {"qubit": "q0", "variant": 0})
    with pytest.raises(ValueError, match=r"3 operations.*maximum of 2"):
        authoring.bind(declaration, {"qubit": "q0", "variant": 1})
    with pytest.raises(ValueError, match=r"depth 2.*maximum of 1"):
        authoring.bind(declaration, {"qubit": "q0", "variant": 2})


def test_program_family_envelope_requires_exact_integer_bounds() -> None:
    with pytest.raises(ValueError, match=r"max_operations.*non-negative integer"):
        authoring.ProgramFamilyEnvelope(
            allowed_gates=(),
            max_operations=1.5,  # pyright: ignore[reportArgumentType]
            max_depth=1,
        )

    with pytest.raises(ValueError, match=r"max_depth.*non-negative integer"):
        authoring.ProgramFamilyEnvelope(
            allowed_gates=(),
            max_operations=1,
            max_depth=True,
        )


def test_program_family_gate_catalog_participates_in_static_program_closure() -> None:
    single = authoring.single_qubit_gate("test.quantum.shared")
    conflicting = authoring.two_qubit_gate("test.quantum.shared")

    @authoring.fragment(
        id="test.quantum.static-gate-catalog",
        envelope=authoring.ProgramFamilyEnvelope(
            allowed_gates=(single,),
            max_operations=1,
            max_depth=1,
        ),
    )
    def family(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return single(qubit)

    with pytest.raises(ValueError, match="conflicting definitions"):

        @authoring.program
        def invalid_catalog(  # pyright: ignore[reportUnusedFunction]
            first: authoring.Qubit,
            second: authoring.Qubit,
        ) -> authoring.QuantumFragment:
            return authoring.sequence(
                family(first),
                conflicting(first, second),
                authoring.measure(first, result="iq"),
            )


def test_fragment_expansion_rejects_results_and_cycles() -> None:
    empty_envelope = authoring.ProgramFamilyEnvelope(
        allowed_gates=(),
        max_operations=1,
        max_depth=1,
    )

    @authoring.fragment(
        id="test.quantum.hidden-result",
        envelope=empty_envelope,
    )
    def hidden_result(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.measure(qubit, result="hidden")

    @authoring.program
    def invalid_result(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.sequence(
            hidden_result(qubit),
            authoring.measure(qubit, result="visible"),
        )

    with pytest.raises(ValueError, match="cannot produce results"):
        authoring.bind(invalid_result, {"qubit": "q0"})

    @authoring.fragment(
        id="test.quantum.recursive",
        envelope=empty_envelope,
    )
    def recursive(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return recursive(qubit)

    @authoring.program
    def invalid_cycle(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.sequence(
            recursive(qubit),
            authoring.measure(qubit, result="iq"),
        )

    with pytest.raises(authoring.ProgramBindingError, match="expansion cycle"):
        authoring.bind(invalid_cycle, {"qubit": "q0"})


def test_program_decorator_rejects_mismatched_ports() -> None:
    x = authoring.single_qubit_gate("x")

    with pytest.raises(TypeError, match="Python annotation is incompatible"):

        @authoring.program
        def mismatched(  # pyright: ignore[reportUnusedFunction]
            qubit: authoring.Qubit,
            count: Annotated[str, GateParameterKind.INTEGER],
        ) -> authoring.QuantumFragment:
            return authoring.sequence(
                authoring.repeat(x(qubit), cast("int", cast("object", count))),
                authoring.measure(qubit, result="iq"),
            )


def test_program_result_named_values_uses_named_access() -> None:
    qubit = authoring.qubit("q0")
    declaration = authoring._close_program(
        "test.quantum.result-values",
        authoring.measure(qubit, result="values"),
    )

    assert declaration.results.values.id == "values"


def test_program_call_owns_domain_effect_shots_and_named_products() -> None:
    x = authoring.single_qubit_gate("x")

    @authoring.program(id="test.quantum.call")
    def x_count(
        qubit: authoring.Qubit,
        count: Annotated[int, GateParameterKind.INTEGER],
    ) -> authoring.QuantumFragment:
        return authoring.sequence(
            authoring.repeat(x(qubit), count),
            authoring.measure(qubit, result="iq_shots"),
        )

    default_call = x_count("q0", 2)
    call = assert_type(
        default_call.with_shots(32),
        authoring.QuantumProgramCall,
    )
    repeated_call = x_count.call("second", "q0", 3)

    assert default_call.domain_call.key == call.domain_call.key
    assert repeated_call.domain_call.key != call.domain_call.key
    assert call.shots == 32
    assert call.arguments == (("qubit", "q0"), ("count", 2))
    assert call.domain_call.id == "call"
    execution = call.domain_call.execution
    assert execution.id == "call/test.quantum.call"
    assert tuple(name for name, _value in execution.input_bindings) == (
        "qubit",
        "count",
    )
    [product] = call.domain_call.product_declarations
    assert product.id == "iq_shots"
    assert product.dtype == "complex128"
    assert product.unit == "ratio"
    assert len(product.axes) == 1
    assert product.axes[0].kind == "shot"
    assert call.results.iq_shots is call.results["iq_shots"]
    assert call.results.iq_shots.id == "call/iq_shots"

    @sc.experiment(id="test.quantum.call-template", kind="x_count")
    def experiment(context: sc.ExperimentContext) -> None:
        results = context.use(call)
        context.alias(results.iq_shots)

    invocation = experiment()
    [selection] = invocation.definition.record_selections
    assert isinstance(selection, RecordSelection)
    assert selection.product_id.qualified_name == "call/iq_shots"


def test_program_call_validates_bound_values_and_shot_count() -> None:
    @authoring.program(id="test.quantum.validated-call")
    def declaration(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.measure(qubit, result="iq")

    with pytest.raises(ValueError, match=r"inputs\.qubit"):
        declaration(1)
    with pytest.raises(ValueError, match="shots"):
        declaration("q0").with_shots(0)


def test_program_results_share_one_explicit_shot_dimension() -> None:
    @authoring.program(id="test.quantum.multi-result")
    def declaration(
        first: authoring.Qubit,
        second: authoring.Qubit,
    ) -> authoring.QuantumFragment:
        return authoring.parallel(
            authoring.measure(first, result="first_iq"),
            authoring.measure(second, result="second_iq"),
        )

    call = declaration("q0", "q1").with_shots(16)

    @sc.experiment(id="test.quantum.multi-result", kind="quantum")
    def experiment(
        context: sc.ExperimentContext,
    ) -> sc.PerEntity[sc.ProductRef]:
        context.use(call)
        return call.entity_results()

    compiled = compile_invocation(experiment())
    bound = bind_program(
        compiled.program,
        build_config_environment(
            load_config_snapshot_document(
                _REPO_ROOT
                / "fixtures"
                / "core"
                / "simple_scan"
                / "config-snapshot.json"
            )
        ),
    )

    dimensions = [
        product.axes[0].dimension_id for product in bound.bindings.product_defs
    ]
    assert len(set(dimensions)) == 1
    assert all(
        product.axes[0].dimension_label == "shot"
        for product in bound.bindings.product_defs
    )
    [record] = plan_records(
        bound.bindings.product_defs,
        bound.bindings.product_uses,
        bound.bindings.product_record_uses,
    )
    assert isinstance(record, EntityRecordPlan)
    assert [axis.kind for axis in record.axes] == ["entity", "shot"]
    assert record.axes[0].index is not None
    assert [entity.id for entity in record.axes[0].index.values] == ["q0", "q1"]


def test_qubit_set_retains_parallel_authoring_and_owns_entity_axis_result() -> None:
    x = authoring.single_qubit_gate("x")

    @authoring.program(id="test.quantum.qubit-set")
    def declaration(qubits: authoring.QubitSet) -> authoring.QuantumFragment:
        return authoring.parallel_each(
            qubits,
            lambda qubit: authoring.sequence(
                x(qubit),
                authoring.measure(qubit, result="iq_shots"),
            ),
        )

    assert [port.id for port in declaration.ports] == ["qubits"]
    assert declaration.draw().splitlines()[1] == "└─ parallel_each $qubits"
    assert declaration.results.iq_shots.entity_set is declaration.entity_sets[0]
    with pytest.raises(ValueError, match="must not be empty"):
        declaration(())
    with pytest.raises(ValueError, match="primary key"):
        declaration(("q0", "q0"))

    bound_program = authoring.bind(
        declaration,
        {"qubits": ("q0", "q1")},
    )
    assert isinstance(bound_program.program.body, QuantumParallelEach)
    assert bound_program.program.body.entity_set_id == "qubits"
    assert [entity.value for entity in bound_program.program.body.entity_ids] == [
        "q0",
        "q1",
    ]
    assert not hasattr(bound_program.program.body, "branches")
    workload = estimate_quantum_program_workload(bound_program.verified)
    assert workload.structural_operation_count == 2
    assert workload.expanded_operation_count == 4
    measurements = tuple(
        operation
        for operation in bound_program.verified.operations
        if isinstance(operation, Measure)
    )
    assert [operation.qubit.value for operation in measurements] == ["q0", "q1"]
    assert [operation.acquisition_slot_id.local_id for operation in measurements] == [
        "iq_shots",
        "iq_shots",
    ]
    assert len({operation.acquisition_slot_id for operation in measurements}) == 2

    reversed_bound = authoring.bind(
        declaration,
        {"qubits": ("q1", "q0")},
    )
    assert {
        operation.qubit: operation.acquisition_slot_id
        for operation in reversed_bound.verified.operations
        if isinstance(operation, Measure)
    } == {operation.qubit: operation.acquisition_slot_id for operation in measurements}

    large_entity_ids = tuple(f"q{index}" for index in range(1_000))
    large_bound = authoring.bind(
        declaration,
        {"qubits": large_entity_ids},
    )
    assert isinstance(large_bound.program.body, QuantumParallelEach)
    assert not hasattr(large_bound.program.body, "branches")
    assert large_bound.program.body.operation == bound_program.program.body.operation
    assert len(large_bound.program.body.entity_ids) == 1_000
    large_workload = estimate_quantum_program_workload(large_bound.verified)
    assert large_workload.structural_operation_count == 2
    assert large_workload.expanded_operation_count == 2_000
    assert large_workload.selected_entity_count == 1_000

    call = declaration(("q0", "q1")).with_shots(16)

    @sc.experiment(id="test.quantum.qubit-set", kind="quantum")
    def experiment(context: sc.ExperimentContext) -> None:
        results = context.use(call)
        context.alias(results.iq_shots)

    compiled = compile_invocation(experiment())
    config = load_config_snapshot_document(
        _REPO_ROOT / "fixtures" / "core" / "simple_scan" / "config-snapshot.json"
    )
    topology = config.system.topology.model_copy(
        update={
            "entities": [
                sc.EntityRef(id="q0", kind="logical_qubit"),
                sc.EntityRef(id="q1", kind="logical_qubit"),
                *config.system.topology.entities[1:],
            ]
        }
    )
    config = config.model_copy(
        update={"system": config.system.model_copy(update={"topology": topology})}
    )
    bound = bind_program(
        compiled.program,
        build_config_environment(config),
    )
    [product] = bound.bindings.product_defs
    assert [axis.kind for axis in product.axes] == ["entity", "shot"]
    assert product.axes[0].entities is not None
    assert [entity.id for entity in product.axes[0].entities] == ["q0", "q1"]
    assert product.axes[1].size == 16


def test_qubit_set_can_resolve_a_topology_selection_intent() -> None:
    @authoring.program(id="test.quantum.topology-selected-readout")
    def declaration(qubits: authoring.QubitSet) -> authoring.QuantumFragment:
        return authoring.parallel_each(
            qubits,
            lambda qubit: authoring.measure(qubit, result="iq_shots"),
        )

    call = declaration(
        authoring.select_qubits(
            3,
            connected=True,
            anchor="q1",
            connection_kind="nearest_neighbor",
        )
    ).with_shots(8)

    @sc.experiment(id="test.quantum.topology-selection", kind="quantum")
    def experiment(context: sc.ExperimentContext) -> None:
        context.alias(context.use(call).iq_shots)

    compiled = compile_invocation(experiment())
    config = load_config_snapshot_document(
        _REPO_ROOT / "fixtures" / "core" / "simple_scan" / "config-snapshot.json"
    )
    topology = Topology(
        entities=[
            sc.EntityRef(id=f"q{index}", kind="logical_qubit") for index in range(4)
        ],
        connections=[
            TopologyConnection(
                id=f"q{index}-q{index + 1}",
                kind="nearest_neighbor",
                endpoints=(f"q{index}", f"q{index + 1}"),
            )
            for index in range(3)
        ],
    )
    config = config.model_copy(
        update={"system": config.system.model_copy(update={"topology": topology})}
    )

    bound = bind_program(compiled.program, build_config_environment(config))

    [resolution] = bound.bindings.topology_entity_sets.values()
    assert isinstance(resolution, TopologyEntitySetResolution)
    assert [entity.id for entity in resolution.entities] == ["q1", "q0", "q2"]
    [product] = bound.bindings.product_defs
    assert product.axes[0].entities == resolution.entities
    prepared = prepare_bound_points(bound)
    [execution] = bound.program.program.domain_executions
    [(input_id, values)] = prepared.bind_domain_inputs(
        execution.id,
        "program",
        ("qubits",),
        (0,),
    )
    assert input_id == "qubits"
    rows = cast("list[dict[str, sc.EntityRef]]", values[0])
    assert [row["qubit"].id for row in rows] == ["q1", "q0", "q2"]


def test_coupler_and_pair_sets_expand_parallel_operations() -> None:
    cz = authoring.two_qubit_gate("cz")

    @authoring.program(id="test.quantum.coupler-set")
    def coupler_program(couplers: authoring.CouplerSet) -> authoring.QuantumFragment:
        return authoring.parallel_each(
            couplers,
            lambda coupler: authoring.play(
                authoring.flux(coupler),
                authoring.constant(
                    duration=sc.Quantity(8, "ns"),
                    amplitude=sc.Quantity(0.2, "arb"),
                ),
            ),
        )

    @authoring.program(id="test.quantum.pair-set")
    def pair_program(pairs: authoring.QubitPairSet) -> authoring.QuantumFragment:
        return authoring.parallel_each(
            pairs,
            lambda pair: authoring.sequence(
                cz(pair.left, pair.right),
                authoring.play(
                    authoring.flux(pair.coupler),
                    authoring.constant(
                        duration=sc.Quantity(8, "ns"),
                        amplitude=sc.Quantity(0.2, "arb"),
                    ),
                ),
            ),
        )

    bound_couplers = authoring.bind(coupler_program, {"couplers": ("c0", "c1")})
    assert isinstance(bound_couplers.program.body, QuantumParallel)
    assert len(bound_couplers.program.body.branches) == 2

    bound_pairs = authoring.bind(
        pair_program,
        {
            "pairs": (
                {"left": "q0", "right": "q1", "coupler": "c0"},
                {"left": "q2", "right": "q3", "coupler": "c1"},
            )
        },
    )
    assert isinstance(bound_pairs.program.body, QuantumParallel)
    assert len(bound_pairs.program.body.branches) == 2
    gate_calls = tuple(
        operation
        for operation in bound_pairs.verified.operations
        if isinstance(operation, GateCall)
    )
    assert [[qubit.value for qubit in call.qubits] for call in gate_calls] == [
        ["q0", "q1"],
        ["q2", "q3"],
    ]


def test_pair_set_can_resolve_a_topology_matching_intent() -> None:
    cz = authoring.two_qubit_gate("cz")

    @authoring.program(id="test.quantum.topology-selected-pairs")
    def declaration(pairs: authoring.QubitPairSet) -> authoring.QuantumFragment:
        return authoring.parallel_each(
            pairs,
            lambda pair: cz(pair.left, pair.right),
        )

    call = declaration(
        authoring.select_qubit_pairs(
            connection_kind="nearest_neighbor",
            matching=0,
        )
    )

    @sc.experiment(id="test.quantum.topology-pairs", kind="quantum")
    def experiment(context: sc.ExperimentContext) -> None:
        context.use(call)

    compiled = compile_invocation(experiment())
    config = load_config_snapshot_document(
        _REPO_ROOT / "fixtures" / "core" / "simple_scan" / "config-snapshot.json"
    )
    topology = Topology(
        entities=[
            *(sc.EntityRef(id=f"q{index}", kind="logical_qubit") for index in range(4)),
            *(
                sc.EntityRef(id=f"c{index}", kind="logical_coupler")
                for index in range(3)
            ),
        ],
        connections=[
            TopologyConnection(
                id=f"q{index}-q{index + 1}",
                kind="nearest_neighbor",
                endpoints=(f"q{index}", f"q{index + 1}"),
                entity_id=f"c{index}",
            )
            for index in range(3)
        ],
    )
    config = config.model_copy(
        update={"system": config.system.model_copy(update={"topology": topology})}
    )

    bound = bind_program(compiled.program, build_config_environment(config))

    [resolution] = bound.bindings.topology_entity_sets.values()
    resolution_rows = cast(
        "tuple[dict[str, sc.EntityRef], ...]",
        resolution.table.rows,
    )
    assert [row["coupler"].id for row in resolution_rows] == ["c0", "c2"]
    prepared = prepare_bound_points(bound)
    [execution] = bound.program.program.domain_executions
    [(_input_id, values)] = prepared.bind_domain_inputs(
        execution.id,
        "program",
        ("pairs",),
        (0,),
    )
    rows = cast("list[dict[str, sc.EntityRef]]", values[0])
    assert [row["coupler"].id for row in rows] == ["c0", "c2"]


def test_program_call_binds_compiler_collection_outside_program_arguments() -> None:
    @authoring.program(id="test.quantum.compiler-collection")
    def declaration(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.measure(qubit, result="iq")

    table_type = sc.TableType(
        columns=(
            sc.TableColumn("qubit", sc.ScalarType(sc.StringType())),
            sc.TableColumn("gain", sc.ScalarType(sc.FloatType())),
        ),
        primary_key=("qubit",),
    )
    qubits = sc.parameter("qubits", table_type)
    call = declaration("q0").with_compiler_inputs(qubits=qubits)
    with_shots = call.with_shots(16)

    assert call.arguments == (("qubit", "q0"),)
    assert call.compiler_arguments == (("qubits", qubits),)
    assert with_shots.compiler_arguments == call.compiler_arguments
    assert with_shots.domain_call.key == call.domain_call.key
    execution = call.domain_call.execution
    assert tuple(port.id for port in execution.program.input_ports) == ("qubit",)
    assert tuple(port.id for port in execution.program.compiler_input_ports) == (
        "qubits",
    )


def test_program_call_captures_scalar_compiler_input_literals() -> None:
    @authoring.program(id="test.quantum.compiler-literal")
    def declaration(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.measure(qubit, result="iq")

    call = declaration("q0").with_compiler_inputs(
        detuning=sc.Quantity(5, "MHz"),
        enabled=True,
    )

    assert tuple(name for name, _value in call.compiler_arguments) == (
        "detuning",
        "enabled",
    )
    compiler_types = tuple(
        port.value_type
        for port in call.domain_call.execution.program.compiler_input_ports
    )
    assert isinstance(compiler_types[0], sc.ScalarType)
    assert compiler_types[0].atom == sc.QuantityType(
        unit="MHz",
        minimum=5,
        maximum=5,
    )
    assert compiler_types[1] == sc.ScalarType(sc.BoolType())


def test_repeated_program_calls_require_explicit_instances() -> None:
    @authoring.program(id="test.quantum.repeated")
    def declaration(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.measure(qubit, result="iq")

    with pytest.raises(ValueError, match="duplicate module domain execution ids"):

        @sc.experiment(id="test.quantum.repeated-defaults")
        def repeated_defaults(
            context: sc.ExperimentContext,
        ) -> None:
            context.use(declaration("q0").with_shots(8))
            context.use(declaration("q0").with_shots(8))

        repeated_defaults()

    left = declaration.call("left", "q0").with_shots(8)
    right = declaration.call("right", "q0").with_shots(8)

    @sc.experiment(id="test.quantum.repeated-explicit")
    def repeated_explicit(context: sc.ExperimentContext) -> None:
        context.use(left)
        context.use(right)

    compile_invocation(repeated_explicit())
    assert left.results.iq.id == "left/iq"
    assert right.results.iq.id == "right/iq"


def test_parent_compute_consumes_program_call_result() -> None:
    @authoring.program(id="test.quantum.discriminate")
    def declaration(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.measure(qubit, result="iq_shots")

    @sc.module
    def discriminate(module: sc.ModuleContext) -> None:
        call = declaration("q0").with_shots(16)
        results = module.use(call)
        assert_type(
            binary_iq_probabilities(
                module,
                results.iq_shots,
                discriminator=BinaryIqDiscriminator(
                    state_0_centroid=IqCentroid(real=-1, imag=0),
                    state_1_centroid=IqCentroid(real=1, imag=0),
                ),
                id="discriminate",
            ),
            BinaryIqProbabilityProducts,
        )

    [lowered] = discriminate.definition.body.measurement_computes
    assert lowered.input_bindings[0][1].qualified_name == "discriminate/iq_shots"
    assert {product.qualified_id for product in discriminate.definition.products} == {
        "discriminate/iq_shots",
        "discriminate/probability_0",
        "discriminate/probability_1",
    }


@pytest.mark.parametrize("scannable", [False, True])
def test_bounded_control_compiles_into_shared_typed_program(scannable: bool) -> None:
    duration = sc.Control(
        "duration",
        default=sc.Quantity(64, "ns"),
        minimum=4,
        maximum=1000,
        scannable=scannable,
    )
    controls = sc.ControlSet((duration,))

    @authoring.program
    def timed_delay(
        qubit: authoring.Qubit,
        duration: Annotated[sc.Quantity, sc.QuantityType(unit="ns", minimum=0)],
    ) -> authoring.QuantumFragment:
        return authoring.delay(authoring.drive(qubit), duration)

    if scannable:

        @sc.experiment(controls=controls)
        def scanned(context: sc.ExperimentContext) -> None:
            context.use(timed_delay("q0", duration.ref))

        invocation = scanned().with_axis(
            sc.axis(duration.ref, [sc.Quantity(4, "ns"), sc.Quantity(1000, "ns")])
        )
    else:

        @sc.experiment(controls=controls)
        def fixed(
            context: sc.ExperimentContext,
            duration: Annotated[sc.Input[sc.Quantity], sc.QuantityType(unit="ns")],
        ) -> None:
            context.use(timed_delay("q0", duration))

        invocation = fixed.bind()
    compiled = compile_invocation(invocation)
    assert compiled.request.inputs == (
        {} if scannable else {"duration": sc.Quantity(64, "ns")}
    )
    assert duration.value_type == sc.ScalarType(
        sc.QuantityType(unit="ns", minimum=4, maximum=1000)
    )
    with pytest.raises(ValueError, match="at least"):
        duration.fixed_axis(sc.Quantity(3, "ns"))
    if scannable:
        with pytest.raises(ValueError, match="at most"):
            sc.axis(duration.ref, [sc.Quantity(1001, "ns")])
