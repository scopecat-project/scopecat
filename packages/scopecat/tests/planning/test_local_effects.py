import pytest
from scopecat_testkit.bound_program import (
    ComputeNodeFixture,
    compute_result,
    instrument_acquisition,
    instrument_invocation,
    observable_product,
    overlay_parameter_cell,
    program_fixture,
)
from scopecat_testkit.expressions import (
    state_property as set_state_property,
)
from scopecat_testkit.local_materialization import operations_of_type
from scopecat_testkit.materialized_effects import (
    config_with_physical_resources,
    materialized_effects_contract,
    materialized_state_properties,
    measurement_projection_contract,
)
from scopecat_testkit.parameter_fixtures import (
    READOUT_FREQUENCY_LOOKUP,
)
from scopecat_testkit.parameter_fixtures import (
    parameters as _parameters,
)

from scopecat.compiler.bound_facts import (
    LogicalResourceRequirement,
    product_axis,
    record_product,
)
from scopecat.compiler.point_domain import PointDomain
from scopecat.compiler.relations.verification import (
    ExpressionTypeBindings,
    RowType,
)
from scopecat.execution.local.program import (
    ApplyStateOperation,
    CollectOperation,
    ComputeOperation,
    InvokeOperation,
)
from scopecat.kernel.instrument_members import InterfaceRef
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.resource_identity import logical_resource_port_id
from scopecat.kernel.state import PayloadRef
from scopecat.kernel.symbols import SymbolId
from scopecat.kernel.value_data import CellValue
from scopecat.kernel.value_types import Int, Payload, Scalar, String
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.program.expressions import (
    parameter_lookup,
    point_col,
)
from scopecat.program.logical import (
    ImplementationId,
    LocalPythonImplementation,
)
from scopecat.program.point_domain import point_axis_values
from scopecat.program.value_graph import (
    ComputeOutput,
    OperationId,
    operation_result_id,
)


def _point_domain(
    column_id: str,
    value_type: Scalar,
    values: tuple[CellValue, ...],
) -> PointDomain:
    return PointDomain(
        axes=(point_axis_values(column_id, value_type, values),),
    )


def test_materialized_effects_contract_summarizes_points_and_state() -> None:
    points = _point_domain(
        "readout_frequency",
        Scalar(QuantityType(unit="GHz")),
        (
            Quantity(value=5.9, unit="GHz"),
            Quantity(value=6.0, unit="GHz"),
        ),
    )
    bindings = ExpressionTypeBindings(
        point_row=RowType.from_table(points.value_type),
    )
    product = observable_product(
        "signal",
        unit="ratio",
    )
    acquisition = instrument_acquisition(
        product,
        resource_port_id="readout",
        interface="test.pulse/v1",
    )
    product_use, record_use = record_product(product)
    spec = program_fixture(
        point_domain=points,
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=logical_resource_port_id("readout"),
                capabilities=(InterfaceRef("test.pulse/v1"),),
            ),
        ),
        parameter_overlays=[
            overlay_parameter_cell(
                "readout_devices",
                row_index=0,
                key={"device_id": "r0"},
                column_id="frequency",
                axis_id="readout_frequency",
                value_type=Scalar(QuantityType(unit="GHz")),
            )
        ],
        state=[
            set_state_property(
                "readout",
                interface_id="test.pulse/v1",
                property_id="frequency",
                value=parameter_lookup(
                    READOUT_FREQUENCY_LOOKUP,
                    key={"device_id": "r0"},
                ),
                bindings=bindings,
            )
        ],
        product_defs=[product],
        instrument_acquisitions=[acquisition],
        product_uses=[product_use],
        record_uses=[record_use],
    )

    test_config = config_with_physical_resources({"readout-a": ("test.pulse/v1",)})
    preview = materialized_effects_contract(
        spec,
        _parameters(),
        config=test_config,
    )
    projection = measurement_projection_contract(
        spec,
        _parameters(),
        config=test_config,
    )

    assert len(preview.points) == 2
    assert projection.coordinate_ids == ("readout_frequency",)
    assert [point.coordinates["readout_frequency"] for point in preview.points] == [
        Quantity(value=5.9, unit="GHz"),
        Quantity(value=6.0, unit="GHz"),
    ]
    assert [
        target.value.root for _, _, target in materialized_state_properties(preview)
    ] == [
        Quantity(value=5.9, unit="GHz"),
        Quantity(value=6.0, unit="GHz"),
    ]


def test_separated_state_groups_have_distinct_operation_ids() -> None:
    points = _point_domain("index", Scalar(Int()), (0,))
    bindings = ExpressionTypeBindings(
        point_row=RowType.from_table(points.value_type),
    )
    drive = logical_resource_port_id("drive")
    enabled = set_state_property(
        "drive",
        interface_id="test.drive/v1",
        property_id="output_enabled",
        value=True,
        bindings=bindings,
    )
    disabled = set_state_property(
        "drive",
        interface_id="test.drive/v1",
        property_id="output_enabled",
        value=False,
        bindings=bindings,
    )
    product = observable_product("signal", unit="ratio")
    acquisition = instrument_acquisition(
        product,
        resource_port_id=drive,
        interface="test.drive/v1",
    )
    product_use, record_use = record_product(product)
    spec = program_fixture(
        point_domain=points,
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=drive,
                capabilities=(InterfaceRef("test.drive/v1"),),
            ),
        ),
        product_defs=[product],
        product_uses=[product_use],
        record_uses=[record_use],
        effects=(enabled, acquisition, disabled),
    )

    preview = materialized_effects_contract(
        spec,
        _parameters(),
        config=config_with_physical_resources({"drive-a": ("test.drive/v1",)}),
    )

    states = operations_of_type(preview, ApplyStateOperation, point_index=0)
    assert len(states) == 2
    assert states[0].operation_id != states[1].operation_id


@pytest.mark.parametrize("point_key", [False, True])
def test_materialized_effects_contract_summarizes_compute_payload_boundary(
    point_key: bool,
) -> None:
    def build_waveform(frequency: Quantity) -> dict[str, object]:
        del frequency
        return {"kind": "waveform"}

    operation_id = OperationId(SymbolId(local_id="build-waveform"))
    result_id = operation_result_id(operation_id)
    drive = logical_resource_port_id("drive")
    spec = program_fixture(
        point_domain=_point_domain("device_id", Scalar(String()), ("r0",)),
        invocations=[
            instrument_invocation(
                id="play-waveform",
                resource_port_id=drive,
                interface="test.play_waveforms/v1",
                operation="play",
                arguments={
                    "program": compute_result(
                        "build-waveform",
                        value_type=Scalar(Payload("waveform_bundle")),
                    )
                },
            )
        ],
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=drive,
                capabilities=(InterfaceRef("test.play_waveforms/v1"),),
            ),
        ),
        compute_nodes=[
            ComputeNodeFixture(
                id=operation_id,
                implementation=LocalPythonImplementation(
                    id=ImplementationId("python.build-waveform.v1"),
                    kernel=build_waveform,
                ),
                result=ComputeOutput(
                    id=result_id,
                    value_type=Scalar(Payload("waveform_bundle")),
                ),
                input_types={"frequency": Scalar(QuantityType(dimension="frequency"))},
                inputs={
                    "frequency": parameter_lookup(
                        READOUT_FREQUENCY_LOOKUP,
                        key={
                            "device_id": point_col("device_id", Scalar(String()))
                            if point_key
                            else "r0"
                        },
                    )
                },
            )
        ],
    )

    preview = materialized_effects_contract(
        spec,
        _parameters(),
        config=config_with_physical_resources({"drive-a": ("test.play_waveforms/v1",)}),
    )

    [step] = operations_of_type(preview, ComputeOperation, point_index=0)
    [read] = preview.parameter_reads
    if point_key:
        assert read.evidence.keyed[0].cells[0].value == Quantity(5.95, "GHz")
    else:
        assert read.evidence.keyed == ()
        assert any(
            lookup.cells[0].value == Quantity(5.95, "GHz")
            for binding in preview.binding_parameter_reads
            for lookup in binding.evidence.keyed
        )
    assert step.payload_slot is not None
    assert (
        preview.points[0].ordinal,
        step.logical_compute_node_id,
        step.payload_slot.schema_id,
    ) == (0, "build-waveform", "waveform_bundle")
    assert step.payload_slot.id == f"{step.operation_id}.payload"
    [invocation] = operations_of_type(preview, InvokeOperation, point_index=0)
    payload_ref = invocation.arguments[0].value.root
    assert isinstance(payload_ref, PayloadRef)
    assert (
        invocation.instrument_id,
        invocation.interface_id,
        invocation.operation_id,
        invocation.arguments[0].id,
        payload_ref.payload_id,
    ) == (
        "drive-a",
        "test.play_waveforms/v1",
        "play",
        "program",
        step.payload_slot.id,
    )


def test_materialized_state_can_reference_a_compute_payload() -> None:
    def build_waveform() -> dict[str, object]:
        return {"kind": "waveform"}

    operation_id = OperationId(SymbolId(local_id="build-waveform"))
    result_id = operation_result_id(operation_id)
    payload_type = Scalar(Payload("waveform_bundle"))
    drive = logical_resource_port_id("drive")
    spec = program_fixture(
        point_domain=_point_domain("index", Scalar(Int()), (0,)),
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=drive,
                capabilities=(InterfaceRef("test.play_waveforms/v1"),),
            ),
        ),
        state=[
            set_state_property(
                drive,
                interface_id="test.play_waveforms/v1",
                property_id="program",
                value=compute_result("build-waveform", value_type=payload_type),
            )
        ],
        compute_nodes=[
            ComputeNodeFixture(
                id=operation_id,
                implementation=LocalPythonImplementation(
                    id=ImplementationId("python.build-waveform.v1"),
                    kernel=build_waveform,
                ),
                result=ComputeOutput(id=result_id, value_type=payload_type),
                input_types={},
            )
        ],
    )

    preview = materialized_effects_contract(
        spec,
        _parameters(),
        config=config_with_physical_resources({"drive-a": ("test.play_waveforms/v1",)}),
    )

    [step] = operations_of_type(preview, ComputeOperation, point_index=0)
    assert step.payload_slot is not None
    [(_point_index, _state, target)] = materialized_state_properties(preview)
    assert target.value.root == PayloadRef(payload_id=step.payload_slot.id)


def test_materialized_effects_groups_shared_typed_compute_result() -> None:
    operation_id = OperationId(SymbolId(local_id="build-waveform"))
    result_id = operation_result_id(operation_id)

    def build_waveform() -> dict[str, object]:
        return {"kind": "waveform"}

    spec = program_fixture(
        point_domain=_point_domain("index", Scalar(Int()), (0,)),
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=logical_resource_port_id("drive-a"),
                capabilities=(InterfaceRef("test.play_waveforms/v1"),),
            ),
        ),
        invocations=[
            instrument_invocation(
                id="play-waveform",
                resource_port_id="drive-a",
                interface="test.play_waveforms/v1",
                operation="play",
                arguments={
                    "program": compute_result(
                        "build-waveform",
                        value_type=Scalar(Payload("waveform_bundle")),
                    )
                },
            ),
            instrument_invocation(
                id="preview-waveform",
                resource_port_id="drive-a",
                interface="test.play_waveforms/v1",
                operation="preview",
                arguments={
                    "program": compute_result(
                        "build-waveform",
                        value_type=Scalar(Payload("waveform_bundle")),
                    )
                },
            ),
        ],
        compute_nodes=[
            ComputeNodeFixture(
                id=operation_id,
                implementation=LocalPythonImplementation(
                    id=ImplementationId("python.build-waveform.v1"),
                    kernel=build_waveform,
                ),
                result=ComputeOutput(
                    id=result_id,
                    value_type=Scalar(Payload("waveform_bundle")),
                ),
                input_types={},
            )
        ],
    )

    preview = materialized_effects_contract(
        spec,
        _parameters(),
        config=config_with_physical_resources({"drive-a": ("test.play_waveforms/v1",)}),
    )

    [step] = operations_of_type(preview, ComputeOperation, point_index=0)
    assert step.payload_slot is not None
    assert step.payload_slot.id == f"{step.operation_id}.payload"
    assert step.payload_slot.schema_id == "waveform_bundle"
    invocations = operations_of_type(preview, InvokeOperation, point_index=0)
    assert [
        (
            invocation.interface_id,
            invocation.operation_id,
            invocation.arguments[0].id,
            invocation.arguments[0].value.root,
        )
        for invocation in invocations
    ] == [
        (
            "test.play_waveforms/v1",
            "play",
            "program",
            PayloadRef(payload_id=step.payload_slot.id),
        ),
        (
            "test.play_waveforms/v1",
            "preview",
            "program",
            PayloadRef(payload_id=step.payload_slot.id),
        ),
    ]


def test_materialized_effects_binds_acquisition_to_its_logical_port() -> None:
    product = observable_product(
        "iq_trace",
        unit="V",
        axes=[
            product_axis(
                "time",
                dimension_id="product/iq_trace/time",
                size=16,
                kind="time",
            )
        ],
    )
    acquisition = instrument_acquisition(
        product,
        resource_port_id="readout",
        interface="test.measure_iq/v1",
    )
    product_use, record_use = record_product(product)
    spec = program_fixture(
        point_domain=_point_domain("index", Scalar(Int()), (0,)),
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=logical_resource_port_id("readout"),
                capabilities=(InterfaceRef("test.measure_iq/v1"),),
            ),
        ),
        product_defs=[product],
        instrument_acquisitions=[acquisition],
        product_uses=[product_use],
        record_uses=[record_use],
    )
    config = config_with_physical_resources({"readout-a": ("test.measure_iq/v1",)})
    preview = materialized_effects_contract(spec, _parameters(), config=config)
    [operation] = operations_of_type(preview, CollectOperation, point_index=0)
    [binding] = operation.result_bindings
    [request] = operation.command.requests
    assert operation.instrument_id == "readout-a"
    assert binding.product_use_ids == (product_use.id,)
    assert request.dimensions[0].id == "time"
    assert product.axes[0].dimension_id == "product/iq_trace/time"
