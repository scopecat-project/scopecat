"""Configuration specialization of bound compiler facts."""

from __future__ import annotations

from scopecat_testkit.bound_program import (
    ComputeNodeFixture,
    DomainExecutionFixture,
    program_fixture,
)
from scopecat_testkit.expressions import (
    state_property,
    verified_scalar_expr,
)
from scopecat_testkit.parameter_fixtures import (
    READOUT_FREQUENCY_LOOKUP,
    parameters,
)

from scopecat.compiler.bound_specialization import (
    specialize_bound_facts,
)
from scopecat.compiler.parameter_overlays import PointParameterOverlay
from scopecat.compiler.point_domain import PointDomain
from scopecat.compiler.relations.context import ParameterRelationData
from scopecat.compiler.relations.verification import (
    ExpressionTypeBindings,
    RowType,
)
from scopecat.compiler.value_resolution import resolve_bound_value
from scopecat.domain.program import DomainInputPort, DomainProgramDef
from scopecat.kernel.quantity import Quantity as QuantityValue
from scopecat.kernel.symbols import SymbolId
from scopecat.kernel.value_types import (
    Float,
    Int,
    Quantity,
    Scalar,
    TableColumn,
)
from scopecat.program.expressions import (
    ComputeResultScalarExpr,
    LiteralScalarExpr,
    PointColumnScalarExpr,
    parameter_lookup,
)
from scopecat.program.logical import (
    ImplementationId,
    LocalPythonImplementation,
)
from scopecat.program.point_domain import (
    PointAxis,
    PointAxisLinear,
    point_axis_linear,
    point_axis_values,
)
from scopecat.program.value_graph import (
    ComputeOutput,
    OperationId,
    operation_result_id,
)


def test_bound_fact_specialization_prunes_dead_compute_nodes() -> None:
    value_type = Scalar(Float())

    def compute(
        name: str, upstream: ComputeNodeFixture | None = None
    ) -> ComputeNodeFixture:
        operation_id = OperationId(SymbolId(local_id=name))
        return ComputeNodeFixture(
            id=operation_id,
            implementation=LocalPythonImplementation(
                id=ImplementationId(f"python.{name}"),
                kernel=lambda: None,
            ),
            result=ComputeOutput(
                id=operation_result_id(operation_id),
                value_type=value_type,
            ),
            input_types=({} if upstream is None else {"upstream": value_type}),
            inputs=(
                {}
                if upstream is None
                else {
                    "upstream": ComputeResultScalarExpr(
                        value_id=upstream.result.id,
                        value_type=value_type,
                    )
                }
            ),
        )

    upstream = compute("upstream")
    live = compute("live", upstream)
    dead = compute("dead")
    state = state_property(
        "drive",
        interface_id="drive",
        property_id="payload",
        value=ComputeResultScalarExpr(
            value_id=live.result.id,
            value_type=value_type,
        ),
    )

    program = program_fixture(
        point_domain=PointDomain(axes=()),
        compute_nodes=(upstream, live, dead),
        state=(state,),
    )
    specialized = specialize_bound_facts(
        program.logical,
        program.bindings,
        parameters=ParameterRelationData(),
    )

    assert specialized.live_compute_ids == frozenset(
        {
            upstream.id,
            live.id,
        }
    )


def test_bound_fact_specialization_preserves_exact_empty_point_composition() -> None:
    integer = Scalar(Int())

    program = program_fixture(
        point_domain=PointDomain(
            axes=(
                point_axis_values("x", integer, (1,)),
                point_axis_values("y", integer, ()),
            ),
        ),
    )
    specialized = specialize_bound_facts(
        program.logical,
        program.bindings,
        parameters=ParameterRelationData(),
    )

    column_ids = tuple(
        column.id for column in specialized.point_domain.value_type.columns
    )
    assert column_ids == ("x", "y")


def test_point_domain_center_reads_base_parameter_before_point_overlay() -> None:
    frequency = Scalar(Quantity(unit="GHz"))
    point_row = RowType((TableColumn("frequency", frequency),))
    point_bindings = ExpressionTypeBindings(
        point_row=point_row,
    )
    center = verified_scalar_expr(
        parameter_lookup(
            READOUT_FREQUENCY_LOOKUP,
            key={"device_id": "r0"},
        ),
        bindings=ExpressionTypeBindings(),
        expected_type=frequency,
    )
    overlaid_lookup = verified_scalar_expr(
        parameter_lookup(
            READOUT_FREQUENCY_LOOKUP,
            key={"device_id": "r0"},
        ),
        bindings=point_bindings,
        expected_type=frequency,
    )
    overlay = PointParameterOverlay(
        table_id="readout_devices",
        row_index=0,
        key={"device_id": "r0"},
        column_id="frequency",
        axis_id="frequency",
        value_type=frequency,
    )
    domain = DomainExecutionFixture(
        id="domain",
        program=DomainProgramDef(
            id="program",
            dialect_id="tests.specialization",
            dialect_version="1",
            body=(),
            input_ports=(DomainInputPort("frequency", frequency),),
        ),
        inputs={"frequency": overlaid_lookup},
    )

    program = program_fixture(
        point_domain=PointDomain(
            axes=(
                point_axis_linear(
                    "frequency",
                    frequency,
                    center,
                    QuantityValue(value=0.2, unit="GHz"),
                    3,
                ),
            )
        ),
        parameter_overlays=(overlay,),
        domain_execution=domain,
    )
    specialized = specialize_bound_facts(
        program.logical,
        program.bindings,
        parameters=parameters(),
    )

    [axis] = specialized.point_domain.axes
    assert isinstance(axis, PointAxis)
    assert isinstance(axis.source, PointAxisLinear)
    center_expression = axis.source.center
    assert isinstance(center_expression, LiteralScalarExpr)
    assert center_expression.value == QuantityValue(value=5.95, unit="GHz")
    assert center_expression.value_type == frequency

    [specialized_domain] = program.logical.program.domain_executions
    input_expression = resolve_bound_value(
        program.logical,
        specialized,
        dict(specialized_domain.inputs)["frequency"],
    )
    assert isinstance(input_expression, PointColumnScalarExpr)
    assert input_expression.name == "frequency"
    assert input_expression.value_type == frequency
    [captured] = specialized.parameter_reads
    assert captured.phase == "specialization"
    assert captured.parameter_scope == "base_configuration"
    assert captured.evidence.keyed[0].cells[0].value == QuantityValue(5.95, "GHz")
    assert (
        "symbolic_overlay:readout_devices.frequency"
        in captured.evidence.incomplete_reasons
    )
    again = specialize_bound_facts(
        program.logical, specialized, parameters=parameters()
    )
    assert again.parameter_reads == specialized.parameter_reads
