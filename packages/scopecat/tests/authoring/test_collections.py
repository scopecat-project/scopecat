from __future__ import annotations

from typing import Annotated

import pytest
from scopecat_testkit.authoring import bind_invocation, load_config
from scopecat_testkit.local_materialization import materialize_local_execution

import scopecat.authoring as authoring
from scopecat.authoring import axis
from scopecat.authoring.experiments import ExperimentInvocation
from scopecat.compiler.bind import BoundPlan
from scopecat.compiler.frontend.elaboration import compose_module
from scopecat.compiler.value_resolution import resolve_bound_value
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.symbols import SymbolId
from scopecat.program.expressions import ComputeResultScalarExpr
from scopecat.program.value_graph import OperationId
from scopecat.program.values import compute as program_compute
from scopecat.program.values import input as program_input
from scopecat.records.config import ConfigProfileSnapshot


def _bind_program(
    invocation: ExperimentInvocation,
    config: ConfigProfileSnapshot,
) -> BoundPlan:
    return bind_invocation(
        invocation,
        config_profile=config,
    )


def _echo_program(*, program: object) -> dict[str, object]:
    return {"program": program}


def _empty_payload() -> dict[str, object]:
    return {}


def _entity_scalar() -> authoring.ScalarType:
    return authoring.ScalarType(authoring.EntityType())


def _gate_table_type() -> authoring.TableType:
    return authoring.TableType(
        columns=(
            authoring.TableColumn("control", _entity_scalar()),
            authoring.TableColumn("target", _entity_scalar()),
        )
    )


def test_nested_module_requires_explicit_input_forwarding() -> None:
    @authoring.module(id="test.nested_port.child")
    def child(
        context: authoring.ModuleContext,
        value: authoring.Input[float],
    ) -> None:
        del context, value

    with pytest.raises(ValueError, match="must connect all inputs"):
        child.instantiate("child")

    @authoring.module(id="test.nested_port.root")
    def root(
        context: authoring.ModuleContext,
        outer_value: authoring.Input[float],
    ) -> None:
        context.use(child.instantiate("child", value=outer_value))

    @authoring.experiment(id="test.nested_port", kind="nested_port")
    def experiment(
        experiment: authoring.ExperimentContext,
        outer_value: authoring.Input[float],
    ) -> None:
        experiment.use(root(authoring.input_ref(outer_value)))

    bind_invocation(
        experiment.build(outer_value=1),
        config_profile=load_config(),
    )


def test_scan_points_are_coerced_by_their_target_type() -> None:
    point = authoring.coordinate(
        "value",
        authoring.ScalarType(authoring.FloatType()),
    )

    @authoring.experiment(id="test.scan_coercion", kind="scan_coercion")
    def experiment(experiment: authoring.ExperimentContext) -> None:
        experiment.grid(axis(point, (1,)))

    resolved = bind_invocation(
        experiment.build(),
        config_profile=load_config(),
    )
    plan = materialize_local_execution(resolved)
    value = plan.points[0].coordinates["value"]

    assert value == 1.0
    assert isinstance(value, float)


def test_scan_points_reject_target_constraint_violation() -> None:
    with pytest.raises(authoring.ValueValidationError) as error:
        axis(
            authoring.coordinate(
                "count",
                authoring.ScalarType(authoring.IntType(minimum=1)),
            ),
            (0,),
        )

    assert error.value.path == ("axis", "values", 0)
    assert error.value.reason == "value must be at least 1"


def test_module_invocation_rejects_quantity_unit_and_table_schema_mismatch() -> None:
    @authoring.module(id="test.quantity_type.child")
    def quantity_child(
        context: authoring.ModuleContext,
        frequency: Annotated[
            authoring.Input[Quantity],
            authoring.ScalarType(authoring.QuantityType(unit="GHz")),
        ],
    ) -> None:
        del context, frequency

    duration = program_input(
        "duration",
        authoring.ScalarType(authoring.QuantityType(unit="ns")),
    )
    with pytest.raises(authoring.ValueValidationError, match=r"Quantity\[GHz\]"):
        quantity_child.instantiate("quantity-child", frequency=duration)

    float_gate_table = authoring.TableType(
        columns=(
            authoring.TableColumn(
                "control",
                authoring.ScalarType(authoring.FloatType()),
            ),
            authoring.TableColumn(
                "target",
                authoring.ScalarType(authoring.FloatType()),
            ),
        )
    )

    @authoring.module(id="test.table_type.child")
    def table_child(
        context: authoring.ModuleContext,
        gates: Annotated[
            authoring.Input[list[dict[str, object]]],
            _gate_table_type(),
        ],
    ) -> None:
        del context, gates

    rows = program_input("rows", float_gate_table)
    with pytest.raises(
        authoring.ValueValidationError,
        match=r"control: Scalar\[Entity\]",
    ):
        table_child.instantiate("table-child", gates=rows)


def test_compute_output_is_a_typed_child_input_edge() -> None:
    pulse = authoring.ScalarType(authoring.PayloadType("pulse"))

    @authoring.module(id="test.compute_edge.child")
    def child(
        context: authoring.ModuleContext,
        program: Annotated[
            authoring.Input[dict[str, object]],
            authoring.PayloadType("pulse"),
        ],
    ) -> None:
        context.compute(
            "consume",
            fn=_echo_program,
            inputs={"program": authoring.input_ref(program)},
            output_type=authoring.ScalarType(authoring.PayloadType("consumed")),
        )

    @authoring.module(id="test.compute_edge.middle")
    def middle(
        context: authoring.ModuleContext,
        program: Annotated[
            authoring.Input[dict[str, object]],
            authoring.PayloadType("pulse"),
        ],
    ) -> None:
        context.use(
            child.instantiate(
                "compute-child",
                program=authoring.input_ref(program),
            )
        )

    @authoring.module(id="test.compute_edge.parent")
    def parent(context: authoring.ModuleContext) -> None:
        produce = context.compute(
            "produce",
            fn=_empty_payload,
            output_type=pulse,
        )
        context.use(middle.instantiate("compute-middle", program=produce))

    assembly = compose_module(
        parent.definition,
    )
    consumer = next(
        operation
        for operation in assembly.compute_nodes
        if operation.id.local_id == "consume"
    )
    program_use = dict(consumer.inputs)["program"]
    producer = next(
        operation
        for operation in assembly.compute_nodes
        if operation.result_id == program_use
    )
    assert producer.id == OperationId(SymbolId(local_id="produce"))

    @authoring.experiment(id="test.compute_edge", kind="compute_edge")
    def experiment(experiment: authoring.ExperimentContext) -> None:
        experiment.use(parent())

    program = _bind_program(experiment.build(), load_config())
    bound_consumer = next(
        node
        for node in program.program.program.compute_nodes
        if node.id.local_id == "consume"
    )
    bound_producer = next(
        node
        for node in program.program.program.compute_nodes
        if node.id.local_id == "produce"
    )
    program_edge = resolve_bound_value(
        program.program,
        program.bindings,
        dict(bound_consumer.inputs)["program"],
    )
    assert isinstance(program_edge, ComputeResultScalarExpr)
    assert bound_producer.result_id.local_id == producer.result_id.local_id
    assert bound_producer.result_id.scope == ("parent", *producer.result_id.scope)
    assert bound_producer.result_type == pulse
    assert program_edge.value_id == bound_producer.result_id
    assert program_edge.value_type == pulse

    @authoring.module(id="test.compute_edge.incompatible")
    def incompatible_child(
        context: authoring.ModuleContext,
        program: Annotated[
            authoring.Input[dict[str, object]],
            authoring.PayloadType("waveform"),
        ],
    ) -> None:
        del context, program

    incompatible_produce = program_compute(
        "produce",
        fn=_empty_payload,
        output_type=pulse,
    )
    with pytest.raises(authoring.ValueValidationError, match=r"Payload\[waveform\]"):
        incompatible_child.instantiate(
            "incompatible-child",
            program=incompatible_produce.output,
        )


def test_explicit_null_is_rejected_as_a_bound_value() -> None:
    @authoring.experiment(id="test.null.required", kind="null")
    def required(
        experiment: authoring.ExperimentContext,
        label: authoring.Input[str],
    ) -> None:
        del experiment, label

    with pytest.raises(CheckFailed) as error:
        bind_invocation(
            required.bind(label=None),
            config_profile=load_config(),
        )
    assert error.value.problems[0].code == "module_input_type_mismatch"
    assert "value must not be null" in error.value.problems[0].message
