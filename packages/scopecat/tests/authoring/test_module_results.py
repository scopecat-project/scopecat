# pyright: reportUnusedFunction=false

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, cast

import pytest
from scopecat_testkit.authoring import bind_invocation, load_config
from scopecat_testkit.expressions import evaluate_scalar

import scopecat as sc
from scopecat.authoring.experiments import ExperimentInvocation
from scopecat.compiler.bind import BoundPlan
from scopecat.compiler.frontend.elaboration import compose_module
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.compiler.relations.context import EvalContext
from scopecat.compiler.value_resolution import resolve_bound_value
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.symbols import SymbolId
from scopecat.program.expressions import ComputeResultScalarExpr, ScalarExpr
from scopecat.program.parameters import ParameterValueContract
from scopecat.program.value_graph import OperationId
from scopecat.program.value_refs import internal_value_ref_source_id
from scopecat.records.config import ConfigProfileSnapshot


def _bind_program(
    invocation: ExperimentInvocation,
    config: ConfigProfileSnapshot,
) -> BoundPlan:
    return bind_invocation(
        invocation,
        config_profile=config,
    )


def _payload_type() -> sc.ScalarType:
    return sc.ScalarType(sc.PayloadType("test.module-result"))


type _PayloadInput = Annotated[
    sc.Input[object],
    sc.ScalarType(sc.PayloadType("test.module-result")),
]
type _FloatInput = Annotated[sc.Input[float], sc.FloatType()]
type _GhzQuantityInput = Annotated[sc.Input[sc.Quantity], sc.QuantityType(unit="GHz")]
type _MhzQuantityInput = Annotated[sc.Input[sc.Quantity], sc.QuantityType(unit="MHz")]
_GHZ_FREQUENCY_TABLE = sc.TableType(
    columns=(
        sc.TableColumn(
            "frequency",
            sc.ScalarType(sc.QuantityType(unit="GHz")),
        ),
    )
)
_MHZ_FREQUENCY_TABLE = sc.TableType(
    columns=(
        sc.TableColumn(
            "frequency",
            sc.ScalarType(sc.QuantityType(unit="MHz")),
        ),
    )
)
type _GhzFrequencyTableInput = Annotated[
    sc.Input[list[dict[str, object]]],
    _GHZ_FREQUENCY_TABLE,
]
type _MhzFrequencyTableInput = Annotated[
    sc.Input[list[dict[str, object]]],
    _MHZ_FREQUENCY_TABLE,
]


def _identity_payload(*, payload: object) -> object:
    return payload


def _capture_pair(*, passthrough: object, shifted: object) -> tuple[object, object]:
    return passthrough, shifted


def _identity_consumed(*, consumed: object) -> object:
    return consumed


@dataclass(frozen=True, slots=True)
class _ExpressionResult:
    passthrough: sc.ValueRef
    shifted: sc.ValueRef


@dataclass(frozen=True, slots=True)
class _DependencyResult:
    value: sc.ValueRef
    parameter: sc.ValueRef
    point: sc.ValueRef


def _producer_module() -> sc.ExperimentModule[sc.ValueRef, ...]:
    payload_type = _payload_type()

    @sc.module(id="test.results.producer")
    def module(context: sc.ModuleContext) -> sc.ValueRef:
        produced = context.compute(
            "produce",
            fn=lambda: {"ok": True},
            output_type=payload_type,
        )
        return produced

    return module


def _consumer_module() -> sc.ExperimentModule[None, ...]:
    payload_type = _payload_type()

    @sc.module(id="test.results.consumer")
    def module(context: sc.ModuleContext, payload: _PayloadInput) -> None:
        context.compute(
            "consume",
            fn=_identity_payload,
            inputs={"payload": sc.input_ref(payload)},
            output_type=payload_type,
        )

    return module


def _float_producer_module() -> sc.ExperimentModule[sc.ValueRef[float], ...]:
    @sc.module(id="test.results.float-producer")
    def module(context: sc.ModuleContext) -> sc.ValueRef[float]:
        return cast(
            "sc.ValueRef[float]",
            context.compute(
                "score",
                fn=lambda: 2.5,
                output_type=sc.ScalarType(sc.FloatType()),
            ),
        )

    return module


def test_nested_export_carries_the_full_instance_source_identity() -> None:
    child = _float_producer_module().instantiate("child")

    @sc.module(id="test.results.record-identity-wrapper")
    def wrapper(context: sc.ModuleContext) -> sc.ValueRef[float]:
        context.use(child)
        return child.result

    outer = wrapper.instantiate("outer")

    assert internal_value_ref_source_id(outer.result) == "outer/child/score"


def test_input_export_inherits_its_bound_value_source_identity() -> None:
    source = _float_producer_module().instantiate("source")

    @sc.module(id="test.results.record-identity-passthrough")
    def passthrough(
        context: sc.ModuleContext,
        value: _FloatInput,
    ) -> sc.ValueRef[float]:
        del context
        return sc.input_ref(value)

    invocation = passthrough.instantiate("passthrough", value=source.result)

    assert internal_value_ref_source_id(invocation.result) == "source/score"


def test_explicit_instances_return_hygienic_compute_values_to_siblings(
    tmp_path: Path,
) -> None:
    producer = _producer_module()
    consumer = _consumer_module()
    first = producer.instantiate("first-producer")
    second = producer.instantiate("second-producer")
    first_consumer = consumer.instantiate(
        "first-consumer",
        payload=first.result,
    )
    second_consumer = consumer.instantiate(
        "second-consumer",
        payload=second.result,
    )

    @sc.module(id="test.results.siblings")
    def root(context: sc.ModuleContext) -> None:
        context.use(first)
        context.use(second)
        context.use(first_consumer)
        context.use(second_consumer)

    assembly = compose_module(root.definition)
    nodes = {operation.id: operation for operation in assembly.compute_nodes}
    results = {operation.result_id: operation for operation in assembly.compute_nodes}

    first_input = dict(
        nodes[
            OperationId(SymbolId(scope=("first-consumer",), local_id="consume"))
        ].inputs
    )["payload"]
    second_input = dict(
        nodes[
            OperationId(SymbolId(scope=("second-consumer",), local_id="consume"))
        ].inputs
    )["payload"]
    assert results[first_input].id == OperationId(
        SymbolId(scope=("first-producer",), local_id="produce")
    )
    assert results[second_input].id == OperationId(
        SymbolId(scope=("second-producer",), local_id="produce")
    )

    call = root()

    @sc.experiment(id="test.results.siblings", kind="module_results")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)

    program = _bind_program(
        experiment_definition.build(),
        load_config(),
    )
    bound_nodes = {node.id: node for node in program.program.program.compute_nodes}
    first_edge = resolve_bound_value(
        program.program,
        program.bindings,
        dict(
            bound_nodes[
                OperationId(
                    SymbolId(scope=("siblings", "first-consumer"), local_id="consume")
                )
            ].inputs
        )["payload"],
    )
    second_edge = resolve_bound_value(
        program.program,
        program.bindings,
        dict(
            bound_nodes[
                OperationId(
                    SymbolId(scope=("siblings", "second-consumer"), local_id="consume")
                )
            ].inputs
        )["payload"],
    )
    assert isinstance(first_edge, ComputeResultScalarExpr)
    assert isinstance(second_edge, ComputeResultScalarExpr)
    first_producer = bound_nodes[
        OperationId(SymbolId(scope=("siblings", "first-producer"), local_id="produce"))
    ]
    second_producer = bound_nodes[
        OperationId(SymbolId(scope=("siblings", "second-producer"), local_id="produce"))
    ]
    assert first_edge.value_id == first_producer.result_id
    assert second_edge.value_id == second_producer.result_id
    assert first_edge.value_type == _payload_type()
    assert second_edge.value_type == _payload_type()


def test_returned_child_value_is_prefixed_when_parent_is_instantiated() -> None:
    producer = _producer_module()
    child_instance = producer.instantiate("child")

    @sc.module(id="test.results.wrapper")
    def wrapper(context: sc.ModuleContext) -> sc.ValueRef:
        context.use(child_instance)
        return child_instance.result

    outer = wrapper.instantiate("outer")
    sink = _consumer_module().instantiate("sink", payload=outer.result)

    @sc.module(id="test.results.nested")
    def root(context: sc.ModuleContext) -> None:
        context.use(outer)
        context.use(sink)

    assembly = compose_module(root.definition)
    sink_node = next(
        operation
        for operation in assembly.compute_nodes
        if operation.id.local_id == "consume"
    )
    sink_input = dict(sink_node.inputs)["payload"]
    results = {operation.result_id: operation for operation in assembly.compute_nodes}
    assert results[sink_input].id == OperationId(
        SymbolId(scope=("outer", "child"), local_id="produce")
    )


def test_nested_compute_results_preserve_exact_typed_result_values(
    tmp_path: Path,
) -> None:
    producer = _producer_module()
    child = producer.instantiate("child")

    @sc.module(id="test.results.typed-result-wrapper")
    def wrapper(context: sc.ModuleContext) -> sc.ValueRef:
        context.use(child)
        return child.result

    first = wrapper.instantiate("alpha.outer")
    second = wrapper.instantiate("beta/outer")
    first_sink = _consumer_module().instantiate(
        "first-sink",
        payload=first.result,
    )
    second_sink = _consumer_module().instantiate(
        "second-sink",
        payload=second.result,
    )

    @sc.module(id="test.results.typed-result-root")
    def root(context: sc.ModuleContext) -> None:
        context.use(first)
        context.use(second)
        context.use(first_sink)
        context.use(second_sink)

    call = root()

    @sc.experiment(id="test.results.typed-result", kind="module_results")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        experiment.use(call)

    program = _bind_program(
        experiment_definition.build(),
        load_config(),
    )
    nodes = {node.id: node for node in program.program.program.compute_nodes}
    expected_type = _payload_type()

    for wrapper_scope, sink_scope in (
        ("alpha.outer", "first-sink"),
        ("beta/outer", "second-sink"),
    ):
        producer_node = nodes[
            OperationId(
                SymbolId(
                    scope=("typed-result-root", wrapper_scope, "child"),
                    local_id="produce",
                )
            )
        ]
        sink_node = nodes[
            OperationId(
                SymbolId(
                    scope=("typed-result-root", sink_scope),
                    local_id="consume",
                )
            )
        ]
        edge = resolve_bound_value(
            program.program,
            program.bindings,
            dict(sink_node.inputs)["payload"],
        )
        assert isinstance(edge, ComputeResultScalarExpr)
        assert producer_node.result_type == expected_type
        assert producer_node.result_id.scope == (
            "typed-result-root",
            wrapper_scope,
            "child",
            "produce",
            "outputs",
        )
        assert producer_node.result_id.local_id == "result"
        assert edge.value_id == producer_node.result_id
        assert edge.value_type == expected_type


def test_passthrough_and_expression_results_bind_instance_inputs() -> None:
    @sc.module(id="test.results.expressions")
    def module(context: sc.ModuleContext, value: _FloatInput) -> _ExpressionResult:
        value_ref = sc.input_ref(value)
        return _ExpressionResult(
            passthrough=value_ref,
            shifted=value_ref + 0.5,
        )

    invocation = module.instantiate("expression-instance", value=1.25)

    @sc.module(id="test.results.expression-consumer")
    def consumer_module(
        context: sc.ModuleContext,
        passthrough: _FloatInput,
        shifted: _FloatInput,
    ) -> None:
        context.compute(
            "capture",
            fn=_capture_pair,
            inputs={"passthrough": passthrough, "shifted": shifted},
            output_type=sc.ScalarType(sc.PayloadType("test.result-capture")),
        )

    consumer = consumer_module.instantiate(
        "consumer",
        passthrough=invocation.result.passthrough,
        shifted=invocation.result.shifted,
    )

    @sc.module(id="test.results.expression-root")
    def root(context: sc.ModuleContext) -> None:
        context.use(invocation)
        context.use(consumer)

    flattened = compose_module(root.definition)
    capture_node = next(
        operation
        for operation in flattened.compute_nodes
        if operation.id.local_id == "capture"
    )
    capture_inputs = dict(capture_node.inputs)
    definitions = {definition.id: definition for definition in flattened.value_defs}
    passthrough = definitions[capture_inputs["passthrough"]]
    assert isinstance(passthrough.source, ScalarExpr)
    assert (
        evaluate_scalar(
            passthrough.source,
            EvalContext(),
        )
        == 1.25
    )
    shifted = definitions[capture_inputs["shifted"]]
    assert isinstance(shifted.source, ScalarExpr)
    assert (
        evaluate_scalar(
            shifted.source,
            EvalContext(),
        )
        == 1.75
    )
    assert isinstance(invocation.result, _ExpressionResult)


def test_direct_result_preserves_its_declared_assignable_input_type() -> None:
    ghz_type = sc.ScalarType(sc.QuantityType(unit="GHz"))

    @sc.module(id="test.results.assignable-direct-result")
    def source(context: sc.ModuleContext, value: _GhzQuantityInput) -> sc.ValueRef:
        del context
        return sc.input_ref(value)

    @sc.module(id="test.results.assignable-direct-result-root")
    def root(context: sc.ModuleContext, value: _MhzQuantityInput) -> None:
        result = context.use(source.instantiate("source", value=sc.input_ref(value)))
        context.compute(
            "capture",
            fn=_identity_consumed,
            inputs={"consumed": result},
            output_type=ghz_type,
        )

    flattened = compose_module(root.definition)
    capture = next(
        node for node in flattened.compute_nodes if node.id.local_id == "capture"
    )
    captured_id = dict(capture.inputs)["consumed"]
    captured = next(
        definition
        for definition in flattened.value_defs
        if definition.id == captured_id
    )

    assert dict(capture.input_types) == {"consumed": ghz_type}
    assert captured.value_type == ghz_type


def test_direct_table_result_preserves_its_declared_assignable_input_type() -> None:
    @sc.module(id="test.results.assignable-direct-table-result")
    def source(context: sc.ModuleContext, rows: _GhzFrequencyTableInput) -> sc.ValueRef:
        del context
        return sc.input_ref(rows)

    @sc.module(id="test.results.assignable-direct-table-result-root")
    def root(context: sc.ModuleContext, rows: _MhzFrequencyTableInput) -> sc.ValueRef:
        return context.use(source.instantiate("source", rows=sc.input_ref(rows)))

    flattened = compose_module(root.definition)

    assert [(port.id, port.value_type) for port in flattened.input_ports] == [
        ("rows", _MHZ_FREQUENCY_TABLE)
    ]


def test_module_result_arithmetic_resolves_during_elaboration() -> None:
    value_type = sc.ScalarType(sc.FloatType())

    @sc.module(id="test.results.expression-boundary")
    def source(context: sc.ModuleContext, value: _FloatInput) -> sc.ValueRef:
        del context
        return sc.input_ref(value)

    source_instance = source.instantiate("source", value=1.0)
    returned = source_instance.result
    shifted = returned + 1.0

    @sc.module(id="test.results.expression-boundary-consumer")
    def consumer_module(context: sc.ModuleContext, consumed: _FloatInput) -> None:
        context.compute(
            "capture",
            fn=_identity_consumed,
            inputs={"consumed": consumed},
            output_type=value_type,
        )

    consumer = consumer_module.instantiate("consumer", consumed=shifted)

    @sc.module(id="test.results.expression-boundary-root")
    def root(context: sc.ModuleContext) -> None:
        context.use(source_instance)
        context.use(consumer)

    flattened = compose_module(root.definition)
    capture_node = next(
        semantic_operation
        for semantic_operation in flattened.compute_nodes
        if semantic_operation.id.local_id == "capture"
    )
    captured = dict(capture_node.inputs)["consumed"]
    definitions = {definition.id: definition for definition in flattened.value_defs}
    shifted_definition = definitions[captured]
    assert isinstance(shifted_definition.source, ScalarExpr)
    assert (
        evaluate_scalar(
            shifted_definition.source,
            EvalContext(),
        )
        == 2.0
    )


def test_result_refs_are_nominally_owned_by_the_used_instance() -> None:
    foreign = _producer_module().instantiate("same")
    selected = _producer_module().instantiate("same")
    sink = _consumer_module().instantiate(
        "sink",
        payload=foreign.result,
    )
    with pytest.raises(CheckFailed) as error:

        @sc.module(id="test.results.nominal")
        def nominal(context: sc.ModuleContext) -> None:
            context.use(selected)
            context.use(sink)

    assert [problem.code for problem in error.value.problems] == [
        "module_result_foreign_instance"
    ]


def test_result_roots_preserve_free_inputs_and_value_provenance() -> None:
    atom_type = sc.FloatType()
    value_type = sc.ScalarType(atom_type)
    parameter = sc.parameter("result_parameter", atom_type)
    point = sc.coordinate("result_point", atom_type)

    @sc.module(id="test.results.roots")
    def source(
        context: sc.ModuleContext,
        value: _FloatInput,
        parameter_value: _FloatInput,
        point_value: _FloatInput,
    ) -> _DependencyResult:
        del context
        return _DependencyResult(
            value=sc.input_ref(value),
            parameter=sc.input_ref(parameter_value),
            point=sc.input_ref(point_value),
        )

    @sc.module(id="test.results.roots.wrapper")
    def wrapper(context: sc.ModuleContext, value: _FloatInput) -> sc.ValueRef:
        result = context.use(
            source.instantiate(
                "source",
                value=sc.input_ref(value),
                parameter_value=0.0,
                point_value=0.0,
            )
        )
        return result.value

    @sc.experiment(id="test.results.roots", kind="results")
    def experiment(
        experiment: sc.ExperimentContext,
        value: _FloatInput,
    ) -> None:
        experiment.use(
            source(
                value=value,
                parameter_value=parameter,
                point_value=point,
            )
        )
        experiment.grid(sc.axis(point, (1.0,)))

    assembly = compile_invocation(experiment.build(value=1.0)).program.program

    assert [
        (port.id, port.value_type) for port in wrapper.definition.interface.imports
    ] == [("value", value_type)]
    assert assembly.parameter_contracts == (
        ParameterValueContract("result_parameter", value_type),
    )
    assert [dependency.id for dependency in assembly.point_dependencies] == [
        "result_point"
    ]
