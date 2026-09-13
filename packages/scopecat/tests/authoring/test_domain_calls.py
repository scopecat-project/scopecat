# pyright: reportUnusedFunction=false

from __future__ import annotations

from typing import Annotated

import pytest
from scopecat_testkit.authoring import bind_invocation, load_config
from scopecat_testkit.domain import domain_call

import scopecat as sc
from scopecat.compiler.frontend.elaboration import compose_module
from scopecat.compiler.frontend.logical_verification import verify_logical_program
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.compiler.value_resolution import resolve_bound_value
from scopecat.domain.program import DomainProgramDef
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.payloads import PayloadValue
from scopecat.planning.domain_bridge import (
    make_domain_batch_request,
    make_domain_call_view,
)
from scopecat.planning.domain_results import domain_result_product_use_ids
from scopecat.planning.point_materialization import prepare_bound_points
from scopecat.program.domain import DomainCall, domain_execution, domain_program
from scopecat.program.expressions import PointColumnScalarExpr
from scopecat.program.products import (
    ModuleProductDecl,
    ProductAxis,
    ProductValueSpec,
)
from scopecat.program.table_values import InputTableSource, LiteralTableSource


def _domain_table_type() -> sc.TableType:
    return sc.TableType(
        columns=(
            sc.TableColumn("id", sc.ScalarType(sc.IntType())),
            sc.TableColumn("gain", sc.ScalarType(sc.FloatType())),
        ),
        primary_key=("id",),
    )


def _domain_module() -> tuple[
    sc.ExperimentModule[sc.ProductRef, ...],
    DomainProgramDef,
    object,
]:
    value_type = sc.ScalarType(sc.IntType(minimum=0))
    body = object()
    program = domain_program(
        "x-count-program",
        dialect_id="test.quantum",
        dialect_version="1",
        body=body,
        inputs={"x_count": value_type},
        results={"counts": {"kind": "counts"}},
    )

    @sc.module(id="test.domain.child")
    def module(
        context: sc.ModuleContext,
        x_count: Annotated[sc.Input[int], sc.IntType(minimum=0)],
    ) -> sc.ProductRef:
        call = domain_call(
            program,
            inputs={"x_count": sc.input_ref(x_count)},
            products={
                "counts": ModuleProductDecl(
                    "counts",
                    value_spec=ProductValueSpec(
                        unit="count",
                        dtype="int64",
                    ),
                )
            },
        )
        context.use(call)
        return call.results.counts

    return module, program, body


def test_domain_execution_rejects_unknown_or_missing_bindings() -> None:
    value_type = sc.ScalarType(sc.IntType())

    @sc.module(id="test.domain.products")
    def product_module(context: sc.ModuleContext) -> sc.ProductRef:
        return context._product("result")

    program = domain_program(
        "program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        inputs={"value": value_type},
        results={"result": None},
    )

    with pytest.raises(ValueError, match="unknown"):
        domain_execution(
            program,
            inputs={"value": 1, "typo": 2},
            results={"result": product_module().result},
        )
    with pytest.raises(ValueError, match="missing"):
        domain_execution(
            program,
            inputs={},
            results={"result": product_module().result},
        )


def test_domain_compiler_inputs_are_a_distinct_typed_namespace() -> None:
    value_type = sc.ScalarType(sc.IntType())
    program = domain_program(
        "program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        inputs={"value": value_type},
        compiler_inputs={"calibration_revision": value_type},
    )

    @sc.module(id="test.domain.compiler-inputs")
    def module(context: sc.ModuleContext) -> None:
        context.use(
            domain_call(
                program,
                inputs={"value": 3},
                compiler_inputs={"calibration_revision": 7},
            )
        )

    semantic = compose_module(module.definition).domain_executions[0]

    assert tuple(port.id for port in semantic.program.input_ports) == ("value",)
    assert tuple(port.id for port in semantic.program.compiler_input_ports) == (
        "calibration_revision",
    )
    assert tuple(name for name, _use in semantic.inputs) == ("value",)
    assert tuple(name for name, _use in semantic.compiler_inputs) == (
        "calibration_revision",
    )


def test_structural_domain_call_captures_external_values_once() -> None:
    value_type = sc.ScalarType(sc.IntType(minimum=0))
    table_type = _domain_table_type()
    program = domain_program(
        "structural-program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        inputs={"count": value_type},
        compiler_inputs={"rows": table_type},
        results={"result": None},
    )
    count_value = sc.parameter("count", sc.IntType(minimum=1))
    rows = sc.parameter("rows", table_type)
    selected_call = domain_call(
        program,
        id="selected",
        inputs={"count": count_value},
        compiler_inputs={"rows": rows},
        products={
            "result": ModuleProductDecl(
                "result",
                value_spec=ProductValueSpec(
                    axes=(ProductAxis("sample", size=count_value),),
                ),
            )
        },
    )

    @sc.module(id="test.domain.structural-wrapper")
    def wrapper(
        context: sc.ModuleContext,
        call: DomainCall,
    ) -> sc.ProductRef:
        return context.use(call).result

    invocation = wrapper(selected_call)
    definition = invocation.module.definition
    assert [port.id for port in definition.interface.imports] == [
        "__structural_0",
        "__structural_1",
    ]
    assert invocation.inputs["__structural_0"] is count_value
    assert invocation.inputs["__structural_1"] is rows
    [execution] = definition.body.domain_executions
    count = dict(execution.input_bindings)["count"]
    assert isinstance(count, sc.ValueRef)
    [product] = definition.body.products
    assert isinstance(product.axes[0].size, sc.ValueRef)
    assert product.axes[0].size.id == count.id
    assert invocation.result.id == "structural-wrapper/selected/result"

    @sc.experiment(id="test.domain.structural-call")
    def experiment(context: sc.ExperimentContext) -> None:
        context.alias(context.use(invocation))

    compile_invocation(experiment.build())


def test_domain_call_result_preserves_its_complete_product_schema() -> None:
    program = domain_program(
        "schema-program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        results={"trace": None},
    )
    axis = ProductAxis("sample", size=16, kind="sample", unit="s")
    selected = domain_call(
        program,
        id="capture",
        products={
            "trace": ModuleProductDecl(
                "trace",
                value_spec=ProductValueSpec(
                    dtype="complex128",
                    unit="V",
                    axes=(axis,),
                ),
            )
        },
    )
    expected = ProductValueSpec(
        dtype="complex128",
        unit="V",
        axes=(axis,),
    )

    [declaration] = selected.product_declarations
    assert declaration.value_spec == expected
    assert selected.results.trace.value_spec == expected
    assert dict(selected.execution.result_bindings)["trace"].value_spec == expected


def test_structural_domain_call_captures_a_parent_table_input() -> None:
    table_type = _domain_table_type()
    program = domain_program(
        "structural-table-program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        compiler_inputs={"rows": table_type},
    )

    @sc.module(id="test.domain.structural-table-child")
    def child(context: sc.ModuleContext, call: DomainCall) -> None:
        context.use(call)

    @sc.module(id="test.domain.structural-table-parent")
    def parent(
        context: sc.ModuleContext,
        rows: Annotated[
            sc.Input[list[dict[str, object]]],
            _domain_table_type(),
        ],
    ) -> None:
        call = domain_call(
            program,
            compiler_inputs={"rows": sc.input_ref(rows)},
        )
        context.use(child(call))

    [instance] = parent.definition.body.child_instances
    assert [port.id for port in instance.module.interface.imports] == ["__structural_0"]
    [binding] = instance.input_bindings
    assert binding.import_id == "__structural_0"
    assert isinstance(binding.source.source, InputTableSource)
    assert binding.source.source.input_id == "rows"


def test_table_module_input_reaches_domain_batch_through_nested_forwarding() -> None:
    table_type = _domain_table_type()
    program = domain_program(
        "table-program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        compiler_inputs={"rows": table_type},
    )

    @sc.module(id="test.domain.table-leaf")
    def leaf(
        context: sc.ModuleContext,
        rows: Annotated[
            sc.Input[list[dict[str, object]]],
            _domain_table_type(),
        ],
    ) -> None:
        context.use(
            domain_call(
                program,
                id="compile",
                compiler_inputs={"rows": sc.input_ref(rows)},
            )
        )

    @sc.module(id="test.domain.table-middle")
    def middle(
        context: sc.ModuleContext,
        rows: Annotated[
            sc.Input[list[dict[str, object]]],
            _domain_table_type(),
        ],
    ) -> None:
        context.use(leaf.instantiate("leaf", rows=sc.input_ref(rows)))

    @sc.module(id="test.domain.table-root")
    def root(
        context: sc.ModuleContext,
        rows: Annotated[
            sc.Input[list[dict[str, object]]],
            _domain_table_type(),
        ],
    ) -> None:
        context.use(middle.instantiate("middle", rows=sc.input_ref(rows)))

    @sc.experiment(id="test.domain.table-forwarding", kind="domain")
    def experiment(
        experiment: sc.ExperimentContext,
        rows: Annotated[
            sc.Input[list[dict[str, object]]],
            _domain_table_type(),
        ],
    ) -> None:
        experiment.use(root(sc.input_ref(rows)))

    bound = bind_invocation(
        experiment.build(rows=[{"id": 1, "gain": 0.5}, {"id": 2, "gain": 0.75}]),
        config_profile=load_config(),
    )

    [execution] = bound.program.program.domain_executions
    table_value_id = dict(execution.compiler_inputs)["rows"]
    assert isinstance(
        bound.program.value_defs[table_value_id].source,
        LiteralTableSource,
    )

    points = prepare_bound_points(bound)
    call = make_domain_call_view(
        bound,
        execution.id,
        domain_result_product_use_ids(bound.bindings, execution),
    )
    request = make_domain_batch_request(
        call,
        points,
        (0,),
        legal_cut_offsets=(1,),
        batch_ordinal=0,
    )
    assert request.inputs.compiler_input("rows") == (
        ({"id": 1, "gain": 0.5}, {"id": 2, "gain": 0.75}),
    )


def test_domain_program_rejects_overlapping_input_namespaces() -> None:
    value_type = sc.ScalarType(sc.IntType())

    with pytest.raises(ValueError, match="ids must be unique"):
        domain_program(
            "program",
            dialect_id="test",
            dialect_version="1",
            body=object(),
            inputs={"value": value_type},
            compiler_inputs={"value": value_type},
        )


def test_domain_execution_captures_literal_inputs_at_authoring_ingress() -> None:
    body = object()
    payload = PayloadValue(schema_id="test.program", payload=body)
    program = domain_program(
        "program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        inputs={"payload": sc.ScalarType(sc.PayloadType("test.program"))},
    )

    execution = domain_execution(program, inputs={"payload": payload})

    captured = execution.input_bindings[0][1]
    assert isinstance(captured, PayloadValue)
    assert captured is payload
    assert captured.schema_id == "test.program"
    assert captured.payload is body

    with pytest.raises(ValueError, match="finite"):
        domain_execution(program, inputs={"payload": float("nan")})


def test_native_domain_call_owns_its_result_products() -> None:
    program = domain_program(
        "program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        results={"result": None},
    )

    call = domain_call(program, id="owned")

    @sc.module(id="test.domain.local")
    def local(context: sc.ModuleContext) -> sc.ProductRef:
        context.use(call)
        return call.results.result

    [declaration] = call.product_declarations
    assert declaration.qualified_id == "owned/result"
    assert call.results.result.id == "owned/result"
    assert local.definition.products[0].qualified_id == "owned/result"


def test_module_preserves_ordered_domain_executions() -> None:
    program = domain_program(
        "program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
    )

    @sc.module(id="test.domain.single")
    def module(context: sc.ModuleContext) -> None:
        context.use(domain_call(program, id="first"))
        context.use(domain_call(program, id="second"))

    assert tuple(call.id for call in module.definition.body.domain_executions) == (
        "first/program",
        "second/program",
    )


def test_composed_domain_effects_are_scoped_per_module_instance() -> None:
    program = domain_program(
        "program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        results={"result": None},
    )

    @sc.module(id="test.domain.reusable")
    def child(context: sc.ModuleContext) -> None:
        context.use(
            domain_call(
                program,
                id="call",
            )
        )

    right = child.instantiate("right")
    left = child.instantiate("left")

    @sc.module(id="test.domain.composed")
    def root(context: sc.ModuleContext) -> None:
        context.use(right)
        context.use(left)

    assembly = compose_module(root.definition)

    assert tuple(execution.id for execution in assembly.domain_executions) == (
        "right/call/program",
        "left/call/program",
    )
    assert tuple(
        execution.results[0][1].qualified_name
        for execution in assembly.domain_executions
    ) == ("right/call/result", "left/call/result")


def test_domain_execution_rejects_execute_stage_compute_input() -> None:
    value_type = sc.ScalarType(sc.IntType())
    program = domain_program(
        "program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        inputs={"value": value_type},
        results={"result": None},
    )

    @sc.module(id="test.domain.execute-input")
    def module(context: sc.ModuleContext) -> None:
        compute = context.compute(
            "compute",
            fn=lambda: 1,
            output_type=value_type,
        )
        context.use(
            domain_call(
                program,
                inputs={"value": compute},
            )
        )

    with pytest.raises(CheckFailed) as error:
        verify_logical_program(compose_module(module.definition))
    assert "logical_domain_execution_input_stage_unavailable" in {
        problem.code for problem in error.value.problems
    }


def test_experiment_domain_execution_lowers_plan_inputs_and_composed_product_uses() -> (
    None
):
    child, _program, body = _domain_module()

    @sc.module(id="test.domain.wrapper")
    def wrapper(
        context: sc.ModuleContext,
        x_count: Annotated[sc.Input[int], sc.IntType(minimum=0)],
    ) -> sc.ProductRef:
        return context.use(child.instantiate("inner", x_count=x_count))

    point_x_count = sc.coordinate(
        "x_count",
        sc.IntType(minimum=0),
    )

    @sc.module(id="test.domain.root")
    def root_module(
        context: sc.ModuleContext,
        x_count: Annotated[sc.Input[int], sc.IntType(minimum=0)],
    ) -> sc.ProductRef:
        outer = wrapper.instantiate("outer", x_count=x_count)
        context.use(outer)
        return outer.result

    assembly = compose_module(root_module.definition, x_count=point_x_count)
    assert len(assembly.domain_executions) == 1
    assert (
        assembly.domain_executions[0].program.symbol_id.qualified_name
        == "x-count-program"
    )
    assert assembly.domain_executions[0].results[0][1].qualified_name == (
        "outer/inner/call/counts"
    )

    @sc.experiment(id="test.domain", kind="domain")
    def experiment(experiment: sc.ExperimentContext) -> None:
        selected_product = experiment.use(root_module(point_x_count))
        experiment.grid(sc.axis(point_x_count, (1, 2)))
        experiment.alias(selected_product, record_id="counts_first")
        experiment.alias(selected_product, record_id="counts_second")

    resolved = bind_invocation(
        experiment.build(),
        config_profile=load_config(),
    )
    typed = resolved.bindings

    assert resolved.program.program.acquisitions == ()
    execution = resolved.program.program.domain_executions[0]
    assert execution.program.body is body
    expression = resolve_bound_value(
        resolved.program,
        typed,
        dict(execution.inputs)["x_count"],
    )
    assert isinstance(expression, PointColumnScalarExpr)
    result_id, product_id = execution.results[0]
    assert product_id.qualified_name == "root/outer/inner/call/counts"
    product_use_ids = typed.domain_result_use_ids[(execution.id, result_id)]
    assert product_use_ids == tuple(use.id for use in typed.product_uses)
    assert len(product_use_ids) == 2


def test_domain_literal_input_namespace_does_not_collide_with_compute() -> None:
    value_type = sc.ScalarType(sc.IntType())
    program = domain_program(
        "program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        inputs={"value": value_type},
        results={"result": None},
    )

    @sc.module(id="test.domain.literal-namespace")
    def module(context: sc.ModuleContext) -> None:
        context.compute(
            "domain",
            fn=_identity_value,
            inputs={"value": 1},
            output_type=value_type,
        )
        context.use(
            domain_call(
                program,
                inputs={"value": 2},
            )
        )

    logical_program = compose_module(module.definition)
    value_ids = {
        definition.id.qualified_name for definition in logical_program.value_defs
    }
    assert "domain/inputs/value" in value_ids
    assert "domain_execution/call%2Fprogram/inputs/value" in value_ids


def _identity_value(value: object) -> object:
    return value
