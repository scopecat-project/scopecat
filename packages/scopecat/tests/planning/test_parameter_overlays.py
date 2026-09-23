from dataclasses import replace
from typing import Never, cast

from scopecat_testkit.bound_program import (
    DomainExecutionFixture,
    bind_program_facts,
    overlay_parameter_cell,
    program_fixture,
)
from scopecat_testkit.expressions import state_property
from scopecat_testkit.local_materialization import materialize_local_execution
from scopecat_testkit.materialized_effects import (
    config_with_physical_resources,
    materialized_state_properties,
)
from scopecat_testkit.parameter_fixtures import (
    PARAMETER_TYPES,
    READOUT_FREQUENCY_LOOKUP,
    parameters,
)

from scopecat.compiler.bound_facts import (
    LogicalResourceRequirement,
)
from scopecat.compiler.parameter_overlays import (
    parameter_cell_bindings,
)
from scopecat.compiler.point_domain import PointDomain
from scopecat.compiler.relations.context import EvalContext, ParameterRelationData
from scopecat.compiler.relations.specialization import (
    specialize_scalar_expression,
)
from scopecat.compiler.relations.verification import (
    ExpressionTypeBindings,
    RowType,
)
from scopecat.config.environment import build_config_environment
from scopecat.domain.program import DomainInputPort, DomainProgramDef
from scopecat.kernel.instrument_members import InterfaceRef
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.resource_identity import logical_resource_port_id
from scopecat.kernel.value_data import CellValue, Row
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_types import Scalar, String, Table, TableColumn
from scopecat.planning.domain_bridge import (
    make_domain_batch_request,
    make_domain_call_view,
)
from scopecat.planning.point_materialization import prepare_bound_points
from scopecat.program.expressions import (
    param,
    parameter_lookup,
    point_col,
)
from scopecat.program.point_domain import (
    PointAxis,
    point_axis_values,
)
from scopecat.program.table_values import ParameterTableSource
from scopecat.records.parameter_read import DomainInputParameterRead

_PARAMETER_TYPES = PARAMETER_TYPES
_DEVICE_ID = Scalar(String())
_FREQUENCY = Scalar(QuantityType(dimension="frequency"))


def _point_domain(
    columns: tuple[TableColumn, ...],
    rows: tuple[tuple[CellValue, ...], ...],
) -> PointDomain:
    factors: list[PointAxis[Never]] = []
    for index, column in enumerate(columns):
        values: list[CellValue] = []
        for row in rows:
            if row[index] not in values:
                values.append(row[index])
        factors.append(point_axis_values(column.id, column.value_type, tuple(values)))
    return PointDomain(axes=tuple(factors))


def _point_bindings(points: PointDomain) -> ExpressionTypeBindings:
    return ExpressionTypeBindings(
        parameters={
            parameter_id: value_type
            for parameter_id, value_type in _PARAMETER_TYPES.items()
            if isinstance(value_type, Scalar)
        },
        point_row=RowType.from_table(points.value_type),
    )


def _frequency_overlay(*, axis_id: str):
    return overlay_parameter_cell(
        "readout_devices",
        row_index=0,
        key={"device_id": "r0"},
        column_id="frequency",
        axis_id=axis_id,
        value_type=_FREQUENCY,
    )


def test_point_parameter_overlay_replaces_only_one_existing_cell() -> None:
    points = _point_domain(
        (
            TableColumn("device_id", _DEVICE_ID),
            TableColumn("frequency", _FREQUENCY),
        ),
        (
            ("r0", Quantity(value=5_900, unit="MHz")),
            ("r0", Quantity(value=6_200, unit="MHz")),
        ),
    )
    point_bindings = _point_bindings(points)
    spec = program_fixture(
        point_domain=points,
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=logical_resource_port_id("readout"),
                capabilities=(InterfaceRef("test.readout/v1"),),
            ),
        ),
        parameter_overlays=[_frequency_overlay(axis_id="frequency")],
        state=[
            state_property(
                "readout",
                interface_id="test.readout/v1",
                property_id="frequency",
                value=parameter_lookup(
                    READOUT_FREQUENCY_LOOKUP,
                    key={"device_id": point_col("device_id", _DEVICE_ID)},
                ),
                bindings=point_bindings,
            )
        ],
    )

    environment = replace(
        build_config_environment(
            config_with_physical_resources({"readout-a": ("test.readout/v1",)})
        ),
        parameters=parameters(),
    )
    base_frequencies = [
        row["frequency"] for row in environment.parameters.table_rows("readout_devices")
    ]
    plan = materialize_local_execution(bind_program_facts(spec, environment))
    without_overlay = materialize_local_execution(
        bind_program_facts(
            replace(
                spec,
                bindings=replace(spec.bindings, parameter_overlays=()),
            ),
            environment,
        )
    )

    assert [
        target.value.root for _, _, target in materialized_state_properties(plan)
    ] == [
        Quantity(value=5.9, unit="GHz"),
        Quantity(value=6.2, unit="GHz"),
    ]
    assert [point.logical_id for point in plan.points] == [
        point.logical_id for point in without_overlay.points
    ]
    assert [
        row["frequency"] for row in environment.parameters.table_rows("readout_devices")
    ] == base_frequencies


def test_domain_compiler_table_is_point_scoped_after_overlay() -> None:
    points = _point_domain(
        (TableColumn("frequency", _FREQUENCY),),
        (
            (Quantity(value=5.9, unit="GHz"),),
            (Quantity(value=6.2, unit="GHz"),),
        ),
    )
    table_type = _PARAMETER_TYPES["readout_devices"]
    assert isinstance(table_type, Table)
    execution = DomainExecutionFixture(
        id="compile",
        program=DomainProgramDef(
            id="consume-readout-table",
            dialect_id="test",
            dialect_version="1",
            body=object(),
            compiler_input_ports=(DomainInputPort("rows", table_type),),
        ),
        compiler_inputs={"rows": ParameterTableSource("readout_devices")},
    )
    spec = program_fixture(
        point_domain=points,
        parameter_overlays=[_frequency_overlay(axis_id="frequency")],
        domain_execution=execution,
    )

    environment = replace(
        build_config_environment(config_with_physical_resources({})),
        parameters=parameters(),
    )
    bound_points = prepare_bound_points(bind_program_facts(spec, environment))
    reads: list[DomainInputParameterRead] = []
    [(input_id, bound_values)] = bound_points.bind_domain_inputs(
        execution.id,
        "compiler",
        ("rows",),
        (0, 1),
        parameter_reads=reads,
    )
    assert [entry.point_ordinal for entry in reads] == [0, 1]
    assert all(
        entry.evidence.incomplete_reasons == ("table_selection:readout_devices",)
        for entry in reads
    )
    bound_tables = cast("tuple[tuple[Row, ...], ...]", bound_values)

    assert input_id == "rows"
    assert [len(rows) for rows in bound_tables] == [2, 2]
    assert [
        next(row["frequency"] for row in rows if row["device_id"] == "r0")
        for rows in bound_tables
    ] == [
        Quantity(value=5.9, unit="GHz"),
        Quantity(value=6.2, unit="GHz"),
    ]


def test_domain_input_materializes_with_its_port_type() -> None:
    generic_frequency = Scalar(QuantityType(dimension="frequency"))
    ghz_frequency = Scalar(QuantityType(dimension="frequency", unit="GHz"))
    execution = DomainExecutionFixture(
        id="consume-frequency",
        program=DomainProgramDef(
            id="frequency-consumer",
            dialect_id="test",
            dialect_version="1",
            body=object(),
            input_ports=(DomainInputPort("frequency", ghz_frequency),),
        ),
        inputs={"frequency": param("frequency", generic_frequency)},
    )
    spec = program_fixture(
        point_domain=PointDomain(axes=()),
        domain_execution=execution,
    )
    environment = replace(
        build_config_environment(config_with_physical_resources({})),
        parameters=ParameterRelationData(
            scalars={
                "frequency": Quantity(value=5_000.0, unit="MHz"),
            }
        ),
    )

    bound_points = prepare_bound_points(bind_program_facts(spec, environment))
    [(input_id, bound_values)] = bound_points.bind_domain_inputs(
        execution.id,
        "program",
        ("frequency",),
        (0,),
    )

    assert input_id == "frequency"
    assert bound_values == (Quantity(value=5.0, unit="GHz"),)
    batch = make_domain_batch_request(
        make_domain_call_view(bound_points.bound_plan, execution.id, ()),
        bound_points,
        (0,),
        legal_cut_offsets=(1,),
        batch_ordinal=0,
    )
    assert batch.inputs.parameter_reads is not None
    [entry] = batch.inputs.parameter_reads
    assert entry.point_ordinal == 0
    assert entry.input_id == "frequency"
    assert entry.evidence.scalars[0].value == Quantity(5000, "MHz")


def test_point_parameter_overlay_residualizes_parameter_lookup() -> None:
    overlay = _frequency_overlay(axis_id="frequency")
    parameters_for_run = parameters()
    cells = parameter_cell_bindings((overlay,))

    result = specialize_scalar_expression(
        parameter_lookup(
            READOUT_FREQUENCY_LOOKUP,
            key={"device_id": "r0"},
        ),
        known=EvalContext(params=parameters_for_run),
        parameter_cells=cells,
    )

    assert result == point_col("frequency", _FREQUENCY)
