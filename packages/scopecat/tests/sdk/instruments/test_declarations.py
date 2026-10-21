from __future__ import annotations

from typing import Annotated, Literal, Protocol

import pytest

from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Int, Payload, Scalar, String
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.sdk.instruments.contracts import StatePropertyRef
from scopecat.sdk.instruments.declarations import (
    Member,
    MemberProjectionField,
    MemberProjectionLayout,
    acquisition,
    argument,
    array_result,
    axis,
    compile_interface,
    declared_acquisition_ref,
    declared_argument_ref,
    declared_interface_layout,
    declared_operation_ref,
    declared_property_ref,
    declared_result_ref,
    instrument_component,
    instrument_interface,
    instrument_member_projection,
    member,
    member_projection_assignments,
    member_projection_field,
    observation,
    operation,
    precondition,
    result_schema,
    scalar_result,
)


@result_schema
class SweepResults:
    frequency = array_result(
        dtype="float64",
        role="coordinate",
        unit="Hz",
        axes=("frequency",),
    )
    response = array_result(
        dtype="complex128",
        id="s_parameter",
        unit="ratio",
        axes=("frequency",),
    )


@instrument_interface("test.source/v1")
class Source(Protocol):
    frequency: Member[Quantity] = member(
        access="read_write", restore=True, unit="Hz", minimum=1.0
    )
    points: Member[int] = member(access="read_write", id="point_count", minimum=2)
    identity: Member[str] = member(access="read_only", capture=False)

    @operation(
        id="set_level",
        invalidates=(frequency,),
    )
    def set_output(
        self,
        level: Annotated[Quantity, argument(id="output_level", unit="dBm")],
        /,
    ) -> None: ...

    @acquisition(
        results=SweepResults,
        axes={
            "frequency": axis(
                size="point_count",
                kind="frequency",
                unit="Hz",
                coordinate_result="frequency",
            )
        },
        preconditions=(
            precondition(
                frequency,
                value=Quantity(5.0, "GHz"),
                unavailable_reason="frequency mismatch",
            ),
        ),
    )
    def sweep(self) -> None: ...


def test_members_compile_from_explicit_attribute_declarations() -> None:
    spec = compile_interface(Source).spec

    assert [
        (item.id, item.access, item.capture, item.restore) for item in spec.properties
    ] == [
        ("frequency", "read_write", True, True),
        ("point_count", "read_write", True, False),
        ("identity", "read_only", False, False),
    ]
    assert spec.properties[0].value_type == Scalar(QuantityType(unit="Hz", minimum=1.0))
    assert spec.properties[1].value_type == Scalar(
        Int(minimum=2, maximum=(1 << 53) - 1)
    )


def test_declared_member_refs_use_interface_owned_properties() -> None:
    assert declared_property_ref(Source, "frequency") == compile_interface(
        Source
    ).ref.property("frequency")
    assert declared_property_ref(Source, "points").property_id == "point_count"
    with pytest.raises(ValueError, match="no property"):
        declared_property_ref(Source, "missing")


def test_operations_preserve_python_parameter_binding_and_wire_ids() -> None:
    spec = compile_interface(Source).spec
    [operation_spec] = spec.operations

    assert operation_spec.id == "set_level"
    assert operation_spec.arguments[0].id == "output_level"
    assert operation_spec.invalidates == [
        StatePropertyRef(interface_id="test.source/v1", property_id="frequency")
    ]
    assert declared_operation_ref(Source, "set_output") == compile_interface(
        Source
    ).ref.operation("set_level")
    assert declared_argument_ref(Source, "set_output", "level").argument_id == (
        "output_level"
    )


def test_acquisition_axes_preconditions_and_results_resolve_members() -> None:
    spec = compile_interface(Source).spec
    [sweep] = spec.acquisitions

    assert sweep.results[0].axes[0].size == StatePropertyRef(
        interface_id="test.source/v1",
        property_id="point_count",
    )
    assert sweep.results[0].axes[0].coordinate_result == "frequency"
    assert sweep.preconditions[0].property == StatePropertyRef(
        interface_id="test.source/v1", property_id="frequency"
    )
    assert declared_acquisition_ref(Source, "sweep") == compile_interface(
        Source
    ).ref.acquisition("sweep")
    assert declared_result_ref(Source, "sweep", "response").result_id == "s_parameter"


def test_result_declarations_are_authoritative_without_value_annotations() -> None:
    @result_schema
    class ExplicitResults:
        count = scalar_result(dtype="int64")
        values = array_result(dtype="complex128", axes=("sample",))

    @instrument_interface("test.explicit_results/v1")
    class ExplicitInterface(Protocol):
        @acquisition(results=ExplicitResults, axes={"sample": axis(size=2)})
        def sample(self) -> None: ...

    [sample] = compile_interface(ExplicitInterface).spec.acquisitions
    assert [(item.id, item.dtype, bool(item.axes)) for item in sample.results] == [
        ("count", "int64", False),
        ("values", "complex128", True),
    ]


def test_result_schema_rejects_schema_inheritance() -> None:
    @result_schema
    class BaseResults:
        value = scalar_result(dtype="float64")

    class DerivedResults(BaseResults):
        other = scalar_result(dtype="float64")

    with pytest.raises(TypeError, match="must not inherit another result schema"):
        result_schema(DerivedResults)


def test_result_schema_rejects_unknown_axes_and_non_none_method_returns() -> None:
    @result_schema
    class UnknownAxisResults:
        values = array_result(dtype="float64", axes=("missing",))

    @instrument_interface("test.unknown_result_axis/v1")
    class UnknownAxisInterface(Protocol):
        @acquisition(results=UnknownAxisResults)
        def sample(self) -> None: ...

    with pytest.raises(ValueError, match="unknown axes"):
        compile_interface(UnknownAxisInterface)

    @instrument_interface("test.invalid_acquisition_return/v1")
    class InvalidReturnInterface(Protocol):
        @acquisition(results=SweepResults, axes={"frequency": axis(size=2)})
        def sample(self) -> None: ...

    InvalidReturnInterface.sample.__annotations__["return"] = SweepResults

    with pytest.raises(TypeError, match="must return None"):
        compile_interface(InvalidReturnInterface)


def test_declared_layout_keeps_interface_as_member_source() -> None:
    compiled = compile_interface(Source)
    layout = declared_interface_layout(compiled)

    assert layout.properties is not None
    assert layout.properties.source_type is Source
    assert [field.python_name for field in layout.properties.fields] == [
        "frequency",
        "points",
        "identity",
    ]
    assert layout.root.operations[0].method_name == "set_output"
    assert layout.root.acquisitions[0].method_name == "sweep"


def test_member_observation_derives_results_from_existing_members() -> None:
    @instrument_interface("test.observed_status/v1")
    class ObservedStatus(Protocol):
        voltage: Member[Quantity] = member(
            access="read_only",
            id="actual_voltage",
            unit="V",
            label="Actual voltage",
        )
        settled: Member[bool] = member(access="read_only")
        readback = observation(voltage, settled, label="Read back status")

    compiled = compile_interface(ObservedStatus)
    [readback] = compiled.spec.acquisitions
    layout = declared_interface_layout(compiled)
    [declared] = layout.root.acquisitions

    assert readback.id == "readback"
    assert [(result.id, result.dtype, result.unit) for result in readback.results] == [
        ("actual_voltage", "float64", "V"),
        ("settled", "bool", None),
    ]
    assert readback.results[0].source_property == StatePropertyRef(
        interface_id="test.observed_status/v1",
        property_id="actual_voltage",
    )
    assert declared.kind == "member_observation"
    assert declared.result.type_name == "ObservedStatusReadbackResults"
    assert [field.python_name for field in declared.result_fields] == [
        "voltage",
        "settled",
    ]
    assert declared_result_ref(ObservedStatus, "readback", "voltage").result_id == (
        "actual_voltage"
    )


@instrument_component(label="Channel")
class Channel(Protocol):
    enabled: Member[bool] = member(access="read_write")


@instrument_interface("test.rack/v1", components={"left": Channel, "right": Channel})
class Rack(Protocol): ...


def test_components_compile_property_members_without_state_carriers() -> None:
    spec = compile_interface(Rack).spec

    assert [component.id for component in spec.components] == ["left", "right"]
    assert spec.components[0].properties[0].id == "enabled"
    assert spec.components[0].properties[0].access == "read_write"


def test_explicit_cross_interface_member_refs_resolve() -> None:
    @instrument_interface("test.monitor/v1")
    class Monitor(Protocol):
        ready: Member[bool] = member(access="read_only")

        @acquisition(
            results=SweepResults,
            axes={"frequency": axis(size=2, kind="frequency")},
            preconditions=(
                precondition(
                    Source.identity,
                    value="SN-1",
                    unavailable_reason="wrong source",
                ),
            ),
        )
        def sample(self) -> None: ...

    [sample] = compile_interface(Monitor).spec.acquisitions
    assert sample.preconditions[0].property == StatePropertyRef(
        interface_id="test.source/v1", property_id="identity"
    )


def test_projection_tracks_presence_without_optional_domain_values() -> None:
    frequency = declared_property_ref(Source, "frequency")
    layout = MemberProjectionLayout((MemberProjectionField("frequency", frequency),))

    @instrument_member_projection(layout)
    class Patch:
        frequency: Quantity = member_projection_field()

    assert member_projection_assignments(Patch()) == {}
    assert member_projection_assignments(Patch(frequency=Quantity(5.0, "GHz"))) == {
        frequency: Quantity(5.0, "GHz")
    }


def test_member_annotation_must_match_declaration_kind() -> None:
    invalid_member: Member[str] = member(access="read_write")

    @instrument_interface("test.invalid_access/v1")
    class Invalid(Protocol):
        status: str = invalid_member  # pyright: ignore[reportAssignmentType]

    with pytest.raises(TypeError, match=r"requires a Member\[T\]"):
        compile_interface(Invalid)


def test_payload_operation_arguments_compile_without_python_wrappers() -> None:
    @instrument_interface("test.payload/v1")
    class PayloadInterface(Protocol):
        @operation()
        def upload(
            self,
            value: Annotated[bytes, argument(payload_schema_id="test.payload/v1")],
        ) -> None: ...

    [upload] = compile_interface(PayloadInterface).spec.operations
    assert upload.arguments[0].value_type.atom == Payload(schema_id="test.payload/v1")


def test_prepared_capability_guidance_survives_compilation() -> None:
    @result_schema
    class Results:
        signal = scalar_result(dtype="float64")

    @instrument_interface("test.prepared/v1")
    class PreparedInterface(Protocol):
        @operation(description="Prepare the payload in the author program first.")
        def execute(
            self,
            program: Annotated[bytes, argument(payload_schema_id="test.program/v1")],
        ) -> None: ...

        @acquisition(
            results=Results,
            description="Read existing results; do not start a new execution.",
        )
        def sample(self) -> None: ...

    spec = compile_interface(PreparedInterface).spec
    assert (
        spec.operations[0].description
        == "Prepare the payload in the author program first."
    )
    assert spec.operations[0].arguments[0].value_type.atom == Payload(
        schema_id="test.program/v1"
    )
    assert (
        spec.acquisitions[0].description
        == "Read existing results; do not start a new execution."
    )


def test_literal_properties_compile_choices() -> None:
    @instrument_interface("test.mode/v1")
    class Mode(Protocol):
        mode: Member[Literal["voltage", "current"]] = member(access="read_only")

    assert compile_interface(Mode).spec.properties[0].value_type == Scalar(
        String(choices=("voltage", "current"))
    )
