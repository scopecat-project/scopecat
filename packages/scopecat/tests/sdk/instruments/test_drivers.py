from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest
from pydantic import ValidationError
from scopecat_testkit.instrument_drivers import (
    SignalInstrumentDriver,
    load_config,
    number_state,
    quantity_state,
)
from scopecat_testkit.signal_instruments import TestSignalInstrumentProvider

from scopecat.kernel.problems import ModelLocation, Problem
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.kernel.value_types import Entity, Float, Int, Payload, Scalar, String
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.planning.provider_validation import instrument_contract_fingerprint
from scopecat.program.measurement_types import MeasurementDType
from scopecat.records.config import instrument_bindings
from scopecat.records.content import command_payload_from_bytes
from scopecat.records.instrument import (
    InstrumentReadback as RecordInstrumentReadback,
)
from scopecat.records.instrument import (
    InstrumentStateObservation,
    ObservationSource,
    state_member_target,
    state_observation,
)
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementUnavailable,
)
from scopecat.sdk.instruments import (
    AcquisitionPreconditionSpec,
    AcquisitionSpec,
    CollectReceipt,
    DriverSuccess,
    InstrumentConnectionContext,
    InstrumentDescription,
    InstrumentProviderContext,
    InstrumentProviderDescription,
    InstrumentStateSnapshot,
    InterfacePropertyImplementationSpec,
    InterfaceRef,
    OperationArgumentSpec,
    PropertyRef,
    PropertySpec,
    StatePropertyRef,
    acquisition,
    acquisition_axis,
    acquisition_precondition,
    acquisition_result,
    bool_property,
    component,
    enum_property,
    float_property,
    instrument_component,
    int_property,
    interface,
    interface_mount,
    operation,
    operation_argument,
    quantity_property,
    state_capture_request,
    string_property,
)
from scopecat.sdk.instruments.backend import lower_backend_apply_request
from scopecat.sdk.instruments.commands import (
    CollectAxisRequest,
    CollectCommand,
    CollectResultRequest,
    InstrumentOperationArgument,
    InstrumentStateAssignment,
    InstrumentStateCommand,
    InteractiveCollectIntent,
    InvokeCommand,
    RejectedInteractiveCollect,
    ResolvedInteractiveCollect,
)
from scopecat.sdk.instruments.contracts import (
    capture_state_members,
    evaluate_acquisition_readiness,
    project_instrument_invoke_state,
    project_instrument_state,
    resolve_acquisition_dimensions,
    resolve_interactive_collect,
    resolve_state_member_spec,
    validate_collect_command,
    validate_collect_plan,
    validate_collect_receipt,
    validate_instrument_description_collection,
    validate_invoke_command,
    validate_reconciled_state_assignments,
    validate_state_capture,
    validate_state_command,
    validate_state_snapshot,
)
from scopecat.sdk.instruments.driver_adapter import (
    lower_state_patch,
    project_state_readback,
)
from scopecat.sdk.instruments.projection import ProjectedInstrumentState


def _observation(
    *,
    interface_id: str,
    property_id: str,
    value: StateValue,
    component_path: list[str] | tuple[str, ...] = (),
) -> InstrumentStateObservation:
    return state_observation(
        PropertyRef(interface_id, tuple(component_path), property_id),
        value,
    )


@pytest.mark.parametrize("dtype", ["bool", "string"])
def test_bool_and_string_acquisition_contracts_reject_units(
    dtype: MeasurementDType,
) -> None:
    with pytest.raises(ValidationError, match="cannot have a unit"):
        acquisition_result("invalid", dtype=dtype, unit="ratio")
    with pytest.raises(ValidationError, match="cannot have a unit"):
        CollectResultRequest(
            id="invalid",
            interface_id="test.readout/v1",
            acquisition_id="sample",
            result_id="invalid",
            dtype=dtype,
            unit="ratio",
        )


@pytest.mark.parametrize(
    ("property_spec", "expected"),
    [
        (bool_property("enabled"), {"id": "enabled", "value_type": {"type": "bool"}}),
        (
            int_property("averages", minimum=1, maximum=16),
            {
                "id": "averages",
                "value_type": {"type": "int", "minimum": 1, "maximum": 16},
            },
        ),
        (float_property("gain"), {"id": "gain", "value_type": {"type": "float"}}),
        (
            string_property("label"),
            {
                "id": "label",
                "value_type": {"type": "string"},
            },
        ),
        (
            enum_property("mode", choices=("standby", "operate")),
            {
                "id": "mode",
                "value_type": {
                    "type": "string",
                    "choices": ["standby", "operate"],
                },
            },
        ),
        (
            quantity_property("frequency", unit="GHz"),
            {
                "id": "frequency",
                "value_type": {"type": "quantity", "unit": "GHz"},
            },
        ),
        (
            quantity_property(
                "delay",
                unit="s",
                minimum=0.0,
                maximum=999.999,
            ),
            {
                "id": "delay",
                "value_type": {
                    "type": "quantity",
                    "unit": "s",
                    "minimum": 0.0,
                    "maximum": 999.999,
                },
            },
        ),
    ],
)
def test_property_spec_has_stable_scalar_wire_format(
    property_spec: PropertySpec,
    expected: dict[str, object],
) -> None:
    compact = property_spec.model_dump(mode="json", exclude_defaults=True)
    restored = PropertySpec.model_validate(compact)
    restored_from_json = PropertySpec.model_validate_json(
        property_spec.model_dump_json()
    )

    assert compact == expected
    assert restored == property_spec
    assert restored_from_json == property_spec


def test_property_restoration_is_an_explicit_safe_lifecycle_policy() -> None:
    assert bool_property("output_enabled").restore is False
    assert bool_property("output_enabled", restore=True).restore is True

    with pytest.raises(ValidationError, match="readable and writable"):
        bool_property("trigger", access="write_only", restore=True)
    with pytest.raises(ValidationError, match="must be captured"):
        bool_property("output_enabled", capture=False, restore=True)


def test_interface_property_implementations_may_only_narrow_contract_policy() -> None:
    reference = StatePropertyRef(
        interface_id="test.fixed/v1",
        property_id="identity",
    )
    description = InstrumentDescription(
        instrument_id="fixed",
        implementation_id="test.fixed",
        implementation_version="1",
        interfaces=[
            interface(
                reference.interface_id,
                properties=[
                    enum_property(
                        reference.property_id,
                        choices=("external", "internal"),
                        access="read_only",
                    )
                ],
            )
        ],
        interface_property_implementations=[
            InterfacePropertyImplementationSpec(
                property=reference,
                access="read_only",
                capture=False,
                restore=False,
                value_type=Scalar(String(choices=("external",))),
            )
        ],
    )

    assert capture_state_members(description) == ()
    resolved = resolve_state_member_spec(
        description,
        state_member_target(PropertyRef(reference.interface_id, (), "identity")),
    )
    assert resolved is not None
    assert resolved.value_type == Scalar(String(choices=("external",)))
    with pytest.raises(ValidationError, match="cannot widen access"):
        InstrumentDescription(
            instrument_id="fixed",
            implementation_id="test.fixed",
            implementation_version="1",
            interfaces=description.interfaces,
            interface_property_implementations=[
                InterfacePropertyImplementationSpec(
                    property=reference,
                    access="read_write",
                    capture=True,
                    restore=False,
                )
            ],
        )
    with pytest.raises(ValidationError, match="cannot widen values"):
        InstrumentDescription(
            instrument_id="fixed",
            implementation_id="test.fixed",
            implementation_version="1",
            interfaces=description.interfaces,
            interface_property_implementations=[
                InterfacePropertyImplementationSpec(
                    property=reference,
                    access="read_only",
                    capture=False,
                    restore=False,
                    value_type=Scalar(String(choices=("external", "unsupported"))),
                )
            ],
        )


def test_operation_contract_declares_state_knowledge_invalidations() -> None:
    output = InterfaceRef("test.output/v1")
    offset = output.property("offset")
    reset = operation("reset", invalidates=(offset,))

    assert reset.invalidates == [
        StatePropertyRef(
            interface_id=output.interface_id,
            property_id=offset.property_id,
        )
    ]
    description = InstrumentDescription(
        instrument_id="source",
        implementation_id="test.source",
        implementation_version="v1",
        interfaces=[
            interface(
                output.interface_id,
                properties=(quantity_property(offset.property_id, unit="V"),),
                operations=(reset,),
            )
        ],
    )
    assert description.interfaces[0].operations[0].invalidates == reset.invalidates


def test_invoke_member_projection_forgets_only_declared_invalidations() -> None:
    output = InterfaceRef("test.output/v1")
    offset = output.property("offset")
    enabled = output.property("enabled")
    description = InstrumentDescription(
        instrument_id="source",
        implementation_id="test.source",
        implementation_version="v1",
        interfaces=[
            interface(
                output.interface_id,
                properties=(
                    quantity_property(offset.property_id, unit="V"),
                    bool_property(enabled.property_id),
                ),
                operations=(operation("reset", invalidates=(offset,)),),
            )
        ],
    )
    baseline = InstrumentStateSnapshot(
        instrument_id="source",
        observations=[
            _observation(
                interface_id=output.interface_id,
                property_id=offset.property_id,
                value=StateValue(Quantity(0.1, "V")),
            ),
            _observation(
                interface_id=output.interface_id,
                property_id=enabled.property_id,
                value=StateValue(True),
            ),
        ],
    )

    projected = project_instrument_invoke_state(
        baseline,
        InvokeCommand(
            command_id="reset-output",
            instrument_id="source",
            resource_id="source",
            interface_id=output.interface_id,
            operation_id="reset",
        ),
        description=description,
    )

    assert [item.target.property_id for item in projected.observations] == ["enabled"]


def test_operation_invalidation_must_reference_declared_state() -> None:
    output = InterfaceRef("test.output/v1")

    with pytest.raises(
        ValidationError,
        match="invalidation must reference a declared property",
    ):
        InstrumentDescription(
            instrument_id="source",
            implementation_id="test.source",
            implementation_version="v1",
            interfaces=[
                interface(
                    output.interface_id,
                    operations=(
                        operation(
                            "reset",
                            invalidates=(output.property("missing"),),
                        ),
                    ),
                )
            ],
        )


def test_property_spec_wire_schema_matches_supported_state_values() -> None:
    schema = PropertySpec.model_json_schema(mode="validation")
    value_schema = schema["properties"]["value_type"]
    definition_name = value_schema["$ref"].rsplit("/", maxsplit=1)[-1]
    alias = schema["$defs"][definition_name]
    wire = schema["$defs"][alias["$ref"].rsplit("/", maxsplit=1)[-1]]
    variants = [
        schema["$defs"][variant["$ref"].rsplit("/", maxsplit=1)[-1]]
        for variant in wire["oneOf"]
    ]

    assert [variant["properties"]["type"]["const"] for variant in variants] == [
        "bool",
        "int",
        "float",
        "string",
        "quantity",
    ]
    assert all(variant["additionalProperties"] is False for variant in variants)
    assert variants[2]["properties"]["finite"]["const"] is True
    assert variants[4]["properties"]["finite"]["const"] is True


def test_operation_argument_spec_supports_opaque_payloads() -> None:
    argument = OperationArgumentSpec(
        id="program",
        value_type=Scalar(Payload(schema_id="pulse_program")),
    )

    assert argument.model_dump(mode="json", exclude_defaults=True) == {
        "id": "program",
        "value_type": {
            "type": "payload",
            "schema_id": "pulse_program",
        },
    }


@pytest.mark.parametrize(
    "value_type",
    [
        Scalar(Entity()),
        Scalar(Payload(schema_id="pulse_program")),
        Scalar(Float(finite=False)),
        {"type": "float", "finite": "false"},
        {"type": "quantity", "minimum": 1.0},
    ],
)
def test_property_spec_rejects_unsupported_or_transient_types(
    value_type: object,
) -> None:
    with pytest.raises(ValidationError):
        PropertySpec.model_validate({"id": "value", "value_type": value_type})


@pytest.mark.parametrize(
    ("state_value", "wire"),
    [
        (StateValue(True), True),
        (StateValue(1), 1),
        (StateValue(1.0), 1.0),
        (StateValue("operate"), "operate"),
        (
            StateValue(Quantity(value=5.0, unit="GHz")),
            {"value": 5.0, "unit": "GHz"},
        ),
        (
            StateValue(PayloadRef(payload_id="program-a")),
            {"payload_id": "program-a"},
        ),
    ],
)
def test_state_value_has_stable_structural_wire_format(
    state_value: StateValue,
    wire: object,
) -> None:
    assert state_value.model_dump(mode="json") == wire
    assert StateValue.model_validate(wire) == state_value
    assert StateValue.model_validate_json(state_value.model_dump_json()) == state_value


def test_state_value_schema_exposes_six_structural_scalar_branches() -> None:
    schema = StateValue.model_json_schema(mode="validation")
    state_schema = schema["$defs"]["StateLiteral"]

    assert state_schema["anyOf"] == [
        {"type": "boolean"},
        {"type": "integer"},
        {"type": "number"},
        {"type": "string"},
        {"$ref": "#/$defs/Quantity"},
        {"$ref": "#/$defs/PayloadRef"},
    ]
    assert schema["$defs"]["PayloadRef"]["required"] == ["payload_id"]
    assert schema["$defs"]["PayloadRef"]["additionalProperties"] is False


def test_concrete_state_values_reject_coercive_and_non_finite_numbers() -> None:
    for value in (Decimal("0.5"), Fraction(1, 2)):
        with pytest.raises(ValidationError):
            StateValue.model_validate(value)
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            StateValue.model_validate(value)
        with pytest.raises(ValidationError):
            StateValue.model_validate({"value": value, "unit": "GHz"})
    for value in (True, "0.5", Decimal("0.5"), Fraction(1, 2)):
        with pytest.raises(ValidationError):
            StateValue.model_validate({"value": value, "unit": "GHz"})
    with pytest.raises(ValidationError):
        StateValue.model_validate({"payload_id": ""})

    assert StateValue.model_validate(True).root is True
    assert StateValue.model_validate(1).root == 1
    assert StateValue.model_validate("0.5").root == "0.5"


def test_instrument_driver_generates_description_and_applies_state() -> None:
    instrument = SignalInstrumentDriver()
    command = _state_command(
        interface_id="test.set_frequency/v1",
        property_id="frequency",
        value=quantity_state(5.0, "GHz"),
    )

    description = instrument.describe()
    readback = project_state_readback(
        instrument.instrument_id,
        instrument.read_state(state_capture_request(description)),
    )
    assert readback.observations
    assert all(
        observation.metadata == {"mode": "test_offline"}
        for observation in readback.observations
    )
    current = InstrumentStateSnapshot(
        instrument_id=readback.instrument_id,
        observations=readback.observations,
    )
    request = lower_backend_apply_request(command)
    patch = lower_state_patch(request)
    result = instrument.apply_state(patch)
    updated = project_instrument_state(
        current,
        command,
        description=description,
    )

    assert description.instrument_id == "source-0"
    assert description.interfaces[0].properties[0].id == "frequency"
    assert description.interfaces[0].properties[0].value_type == Scalar(
        QuantityType(unit="GHz")
    )
    assert isinstance(result, DriverSuccess)
    assert instrument.applied[0] == patch
    assert updated.observations[0].value == quantity_state(5.0, "GHz")
    with pytest.raises(ValidationError):
        updated.observations[0].value.root = 1.0
    updated_quantity = updated.observations[0].value.root
    assert isinstance(updated_quantity, Quantity)
    with pytest.raises(ValidationError):
        updated_quantity.value = 6.0


def test_instrument_driver_validator_checks_declared_property_shapes() -> None:
    description = SignalInstrumentDriver().describe()

    unsupported = validate_state_command(
        command=_state_command(
            interface_id="test.set_frequency/v1",
            property_id="amplitude",
            value=quantity_state(1.0, "GHz"),
        ),
        description=description,
    )
    unit_mismatch = validate_state_command(
        command=_state_command(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=quantity_state(1.0, "ns"),
        ),
        description=description,
    )
    type_mismatch = validate_state_command(
        command=_state_command(
            interface_id="test.set_gain/v1",
            property_id="gain",
            value=quantity_state(1.0, "GHz"),
        ),
        description=description,
    )

    assert unsupported[0].code == "instrument_driver_unsupported_member"
    assert unit_mismatch[0].code == "instrument_driver_property_value_mismatch"
    assert type_mismatch[0].code == "instrument_driver_property_value_mismatch"


def test_instrument_driver_validator_rejects_writes_to_read_only_properties() -> None:
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.read_only_driver",
        implementation_version="v0",
        interfaces=[
            interface(
                "test.status/v1",
                properties=[
                    quantity_property(
                        "temperature",
                        unit="K",
                        access="read_only",
                    )
                ],
            )
        ],
    )

    problems = validate_state_command(
        command=_state_command(
            interface_id="test.status/v1",
            property_id="temperature",
            value=quantity_state(0.02, "K"),
        ),
        description=description,
    )

    assert [item.code for item in problems] == ["instrument_driver_read_only_property"]


def test_instrument_driver_validator_allows_write_only_commands() -> None:
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.write_only_driver",
        implementation_version="v0",
        interfaces=[
            interface(
                "test.secret/v1",
                properties=[
                    string_property(
                        "token",
                        access="write_only",
                    )
                ],
            )
        ],
    )

    problems = validate_state_command(
        command=_state_command(
            interface_id="test.secret/v1",
            property_id="token",
            value=StateValue("configured"),
        ),
        description=description,
    )

    assert problems == []


def test_instrument_driver_validator_applies_scalar_constraints() -> None:
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.constraint_driver",
        implementation_version="v0",
        interfaces=[
            interface(
                "test.set_gain/v1",
                properties=[
                    PropertySpec(
                        id="gain",
                        value_type=Scalar(Float(minimum=0.0, maximum=1.0)),
                    )
                ],
            ),
            interface(
                "test.set_frequency/v1",
                properties=[
                    PropertySpec(
                        id="frequency",
                        value_type=Scalar(
                            QuantityType(unit="GHz", minimum=4.0, maximum=6.0)
                        ),
                    )
                ],
            ),
        ],
    )

    valid_gain = validate_state_command(
        command=_state_command(
            interface_id="test.set_gain/v1",
            property_id="gain",
            value=number_state(0.5),
        ),
        description=description,
    )
    out_of_range_gain = validate_state_command(
        command=_state_command(
            interface_id="test.set_gain/v1",
            property_id="gain",
            value=number_state(2.0),
        ),
        description=description,
    )
    compatible_frequency = validate_state_command(
        command=_state_command(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=quantity_state(5_000.0, "MHz"),
        ),
        description=description,
    )
    out_of_range_frequency = validate_state_command(
        command=_state_command(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=quantity_state(7.0, "GHz"),
        ),
        description=description,
    )
    implicit_unit_frequency = validate_state_command(
        command=_state_command(
            interface_id="test.set_frequency/v1",
            property_id="frequency",
            value=number_state(5.0),
        ),
        description=description,
    )

    assert valid_gain == []
    assert compatible_frequency == []
    assert out_of_range_gain[0].code == "instrument_driver_property_value_mismatch"
    assert isinstance(out_of_range_gain[0].location, ModelLocation)
    assert out_of_range_gain[0].location.path == ("assignments", 0, "value")
    assert out_of_range_frequency[0].code == "instrument_driver_property_value_mismatch"
    assert implicit_unit_frequency == []


def test_instrument_driver_validator_checks_payload_references_and_schemas() -> None:
    payload = command_payload_from_bytes(
        id="program-a",
        schema_id="pulse_program",
        codec_id="tests.canonical-json",
        codec_version=1,
        media_type="application/json",
        content=b'{"samples":[0.0]}',
    )
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.payload_driver",
        implementation_version="v0",
        interfaces=[
            interface(
                "test.play_program/v1",
                operations=[
                    operation(
                        "play",
                        arguments=[
                            operation_argument(
                                "program",
                                value_type=Scalar(Payload(schema_id="pulse_program")),
                            )
                        ],
                    )
                ],
            )
        ],
    )
    wrong_schema_description = description.model_copy(
        update={
            "interfaces": [
                interface(
                    "test.play_program/v1",
                    operations=[
                        operation(
                            "play",
                            arguments=[
                                operation_argument(
                                    "program",
                                    value_type=Scalar(
                                        Payload(schema_id="readout_program")
                                    ),
                                )
                            ],
                        )
                    ],
                )
            ]
        }
    )
    command_with_payload = InvokeCommand(
        command_id="invoke-program",
        instrument_id="source-0",
        resource_id="source-0",
        interface_id="test.play_program/v1",
        operation_id="play",
        arguments=[
            InstrumentOperationArgument(
                id="program",
                value=StateValue(PayloadRef(payload_id=payload.id)),
            )
        ],
        payloads={payload.id: payload},
    )
    command_wire = command_with_payload.model_dump(mode="json")

    assert command_wire["payloads"][payload.id]["schema_id"] == "pulse_program"
    assert command_wire["payloads"][payload.id]["codec_id"] == "tests.canonical-json"
    assert command_wire["payloads"][payload.id]["body"]["kind"] == "inline"
    assert (
        InvokeCommand.model_validate_json(command_with_payload.model_dump_json())
        == command_with_payload
    )

    valid = validate_invoke_command(
        command=command_with_payload,
        description=description,
    )
    with pytest.raises(ValidationError, match="missing referenced payload"):
        InvokeCommand(
            command_id="missing-payload",
            instrument_id="source-0",
            resource_id="source-0",
            interface_id="test.play_program/v1",
            operation_id="play",
            arguments=[
                InstrumentOperationArgument(
                    id="program",
                    value=StateValue(PayloadRef(payload_id="missing-program")),
                )
            ],
            payloads={payload.id: payload},
        )
    not_a_reference = validate_invoke_command(
        command=InvokeCommand(
            command_id="non-reference-payload",
            instrument_id="source-0",
            resource_id="source-0",
            interface_id="test.play_program/v1",
            operation_id="play",
            arguments=[
                InstrumentOperationArgument(
                    id="program",
                    value=StateValue("program-a"),
                )
            ],
        ),
        description=description,
    )
    wrong_schema = validate_invoke_command(
        command=command_with_payload,
        description=wrong_schema_description,
    )

    assert valid == []
    assert (
        not_a_reference[0].code == "instrument_driver_operation_argument_value_mismatch"
    )
    assert wrong_schema[0].code == "instrument_driver_operation_argument_value_mismatch"


def test_provider_builds_fresh_drivers() -> None:
    class Provider:
        @property
        def provider_id(self) -> str:
            return "tests.driver_provider"

        def describe(
            self, context: InstrumentProviderContext
        ) -> InstrumentProviderDescription:
            assert [binding.id for binding in context.bindings] == ["source-0"]
            return InstrumentProviderDescription(
                provider_id=self.provider_id,
                instruments=(SignalInstrumentDriver().describe(),),
            )

        def connect(
            self, context: InstrumentConnectionContext
        ) -> SignalInstrumentDriver:
            assert context.binding.id == "source-0"
            return SignalInstrumentDriver()

    provider = Provider()
    context = InstrumentProviderContext(bindings=instrument_bindings(load_config()))
    connection = InstrumentConnectionContext(
        binding=context.bindings[0],
    )
    first = provider.connect(connection)
    second = provider.connect(connection)

    description = provider.describe(context)
    assert description.provider_id == "tests.driver_provider"
    assert [item.instrument_id for item in description.instruments] == ["source-0"]
    assert first is not second


def test_provider_description_resolves_instruments_from_config() -> None:
    context = InstrumentProviderContext(bindings=instrument_bindings(load_config()))

    description = TestSignalInstrumentProvider().describe(context)

    assert description.problems == ()
    assert [instrument.instrument_id for instrument in description.instruments] == [
        "source-0"
    ]
    assert [interface.id for interface in description.instruments[0].interfaces] == [
        "test.set_frequency/v1",
        "test.scalar_signal/v1",
    ]


def test_provider_description_reports_dynamic_selection_errors() -> None:
    context = InstrumentProviderContext(bindings=instrument_bindings(load_config()))

    description = TestSignalInstrumentProvider(instrument_id="missing-source").describe(
        context
    )

    assert description.instruments == ()
    assert [problem.code for problem in description.problems] == [
        "test_signal_provider_unknown_instrument"
    ]


def test_provider_description_rejects_duplicate_instrument_ids() -> None:
    instrument = SignalInstrumentDriver().describe()

    with pytest.raises(ValueError, match="unique instrument ids: source-0"):
        InstrumentProviderDescription(
            provider_id="tests.duplicate-provider",
            instruments=(instrument, instrument),
        )


def test_provider_description_rejects_conflicting_interface_specs() -> None:
    def described(instrument_id: str, property_id: str) -> InstrumentDescription:
        return InstrumentDescription(
            instrument_id=instrument_id,
            implementation_id=f"tests.{instrument_id}",
            implementation_version="v1",
            interfaces=[
                interface(
                    "test.shared_source/v1",
                    properties=[float_property(property_id)],
                )
            ],
        )

    with pytest.raises(ValueError, match="one stable specification"):
        InstrumentProviderDescription(
            provider_id="tests.conflicting-interfaces",
            instruments=(
                described("source-a", "level"),
                described("source-b", "amplitude"),
            ),
        )


def test_collect_command_rejects_duplicate_request_ids() -> None:
    request = CollectResultRequest(
        id="signal",
        interface_id="test.scalar_signal/v1",
        acquisition_id="sample",
        result_id="signal",
    )

    with pytest.raises(ValidationError, match="request ids must be unique"):
        CollectCommand(
            command_id="duplicate-results",
            instrument_id="source-0",
            point_index=0,
            point_count=1,
            requests=[request, request],
        )


def test_state_command_requires_one_assignment() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        InstrumentStateCommand(
            command_id="empty-apply",
            instrument_id="source-0",
            assignments=[],
        )


def test_collect_command_requires_one_acquisition() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        CollectCommand(
            command_id="empty-collect",
            instrument_id="source-0",
            point_index=0,
            point_count=1,
            requests=[],
        )

    with pytest.raises(ValidationError, match="exactly one acquisition"):
        CollectCommand(
            command_id="mixed-acquisitions",
            instrument_id="source-0",
            point_index=0,
            point_count=1,
            requests=[
                CollectResultRequest(
                    id="first",
                    interface_id="test.scalar_signal/v1",
                    acquisition_id="sample",
                    result_id="signal",
                ),
                CollectResultRequest(
                    id="second",
                    interface_id="test.scalar_signal/v1",
                    acquisition_id="alternate",
                    result_id="signal",
                ),
            ],
        )


def test_instrument_description_rejects_duplicate_interface_members() -> None:
    with pytest.raises(ValidationError, match="property ids must be unique"):
        interface(
            "test.source/v1",
            properties=[float_property("level"), float_property("level")],
        )
    with pytest.raises(
        ValidationError,
        match="acquisition result ids must be unique",
    ):
        acquisition(
            "sample",
            results=[
                acquisition_result("signal"),
                acquisition_result("signal"),
            ],
        )
    duplicate_interface = interface("test.source/v1")
    with pytest.raises(
        ValidationError,
        match="instrument interface ids must be unique",
    ):
        InstrumentDescription(
            instrument_id="source-0",
            implementation_id="tests.duplicate_interfaces",
            implementation_version="v0",
            interfaces=[duplicate_interface, duplicate_interface],
        )


def test_acquisition_schema_is_one_flat_contract() -> None:
    acquisition_spec = acquisition(
        "sample",
        results=[acquisition_result("signal")],
    )
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.flat_acquisition",
        implementation_version="v1",
        interfaces=[
            interface(
                "test.monitor/v1",
                acquisitions=[acquisition_spec],
            )
        ],
    )
    restored = InstrumentDescription.model_validate(description.model_dump(mode="json"))
    restored_acquisition = restored.interfaces[0].acquisitions[0]

    assert isinstance(acquisition_spec, AcquisitionSpec)
    assert "kind" not in acquisition_spec.model_dump(mode="json")
    assert isinstance(restored_acquisition, AcquisitionSpec)
    assert [result.id for result in restored_acquisition.results] == ["signal"]


def test_acquisition_readiness_uses_public_state_preconditions() -> None:
    output_enabled = (
        InterfaceRef("test.source/v1").component("output").property("output_enabled")
    )
    description = _monitor_collect_description(
        preconditions=(
            acquisition_precondition(
                output_enabled,
                value=True,
                unavailable_reason="Source output is disabled.",
            ),
        )
    )
    acquisition_spec = description.interfaces[1].acquisitions[0]

    unknown = evaluate_acquisition_readiness(
        description=description,
        acquisition=acquisition_spec,
        state=None,
    )
    blocked = evaluate_acquisition_readiness(
        description=description,
        acquisition=acquisition_spec,
        state=_monitor_collect_snapshot(),
    )
    ready = evaluate_acquisition_readiness(
        description=description,
        acquisition=acquisition_spec,
        state=_monitor_collect_snapshot(output_enabled=True),
    )

    assert unknown.status == "unknown"
    assert blocked.status == "blocked"
    assert [issue.reason for issue in blocked.issues] == ["Source output is disabled."]
    assert ready.status == "ready"
    assert ready.issues == ()


def test_collect_plan_checks_preconditions_but_structural_validation_does_not() -> None:
    description = _monitor_collect_description(
        preconditions=(
            acquisition_precondition(
                InterfaceRef("test.source/v1")
                .component("output")
                .property("output_enabled"),
                value=True,
                unavailable_reason="Source output is disabled.",
            ),
        )
    )
    command = _monitor_collect_command(
        "monitored_current",
        unit="A",
    )

    structural = validate_collect_command(
        command=command,
        description=description,
    )
    planned = validate_collect_plan(
        command=command,
        description=description,
        baseline=_monitor_collect_snapshot(),
    )

    assert structural == []
    assert [problem.code for problem in planned] == [
        "instrument_driver_acquisition_precondition_not_met"
    ]
    [problem] = planned
    assert isinstance(problem.location, ModelLocation)
    assert problem.location.root == "instrument_collect_command"
    assert isinstance(problem.related_locations[0], ModelLocation)
    assert problem.related_locations[0].root == "instrument_state"
    assert problem.related_locations[0].path == (
        "test.source/v1",
        "output",
        "output_enabled",
    )
    assert problem.details["observed_value"] is False


def test_known_precondition_blocks_with_projected_state() -> None:
    description = _monitor_collect_description(
        preconditions=(
            acquisition_precondition(
                InterfaceRef("test.source/v1")
                .component("output")
                .property("output_enabled"),
                value=True,
                unavailable_reason="Source output is disabled.",
            ),
        )
    )
    partial = ProjectedInstrumentState(
        instrument_id="source-0",
        observations=(
            _observation(
                interface_id="test.source/v1",
                component_path=["output"],
                property_id="output_enabled",
                value=StateValue(False),
            ),
        ),
    )

    planned = validate_collect_plan(
        command=_monitor_collect_command(
            "monitored_current",
            unit="A",
        ),
        description=description,
        baseline=partial,
    )

    assert [problem.code for problem in planned] == [
        "instrument_driver_acquisition_precondition_not_met"
    ]


def test_acquisition_preconditions_require_a_declared_observable_property() -> None:
    with pytest.raises(
        ValidationError,
        match="precondition must reference a declared property",
    ):
        _monitor_collect_description(
            preconditions=(
                acquisition_precondition(
                    InterfaceRef("test.source/v1")
                    .component("output")
                    .property("missing"),
                    value=True,
                    unavailable_reason="Missing state.",
                ),
            )
        )

    source = InterfaceRef("test.source/v1")
    with pytest.raises(
        ValidationError,
        match="precondition property must be observable",
    ):
        InstrumentDescription(
            instrument_id="source-0",
            implementation_id="tests.write_only_precondition",
            implementation_version="v1",
            interfaces=[
                interface(
                    source.interface_id,
                    properties=[bool_property("enabled", access="write_only")],
                    acquisitions=[
                        acquisition(
                            "sample",
                            results=[acquisition_result("reading")],
                            preconditions=[
                                acquisition_precondition(
                                    source.property("enabled"),
                                    value=True,
                                    unavailable_reason="Output disabled.",
                                )
                            ],
                        )
                    ],
                )
            ],
        )


def test_quantity_preconditions_require_a_canonical_property_unit() -> None:
    source = InterfaceRef("test.source/v1")
    level = source.property("level")

    with pytest.raises(
        ValidationError,
        match="quantity precondition property must declare a canonical unit",
    ):
        InstrumentDescription(
            instrument_id="source-0",
            implementation_id="tests.dimension_only_precondition",
            implementation_version="v1",
            interfaces=[
                interface(
                    source.interface_id,
                    properties=[
                        PropertySpec(
                            id=level.property_id,
                            value_type=Scalar(QuantityType(dimension="voltage")),
                        )
                    ],
                    acquisitions=[
                        acquisition(
                            "sample",
                            results=[acquisition_result("reading", unit="V")],
                            preconditions=[
                                acquisition_precondition(
                                    level,
                                    value=Quantity(1.0, "V"),
                                    unavailable_reason="Level is not one volt.",
                                )
                            ],
                        )
                    ],
                )
            ],
        )


def test_quantity_preconditions_compare_projected_compatible_units() -> None:
    level = InterfaceRef("test.level/v1").property("level")
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.quantity_precondition",
        implementation_version="v1",
        interfaces=[
            interface(
                "test.level/v1",
                properties=[quantity_property("level", unit="V")],
                acquisitions=[
                    acquisition(
                        "sample",
                        results=[acquisition_result("reading", unit="V")],
                        preconditions=[
                            acquisition_precondition(
                                level,
                                value=Quantity(1.0, "V"),
                                unavailable_reason="Level is not one volt.",
                            )
                        ],
                    )
                ],
            )
        ],
    )
    baseline = InstrumentStateSnapshot(
        instrument_id="source-0",
        observations=[
            _observation(
                interface_id="test.level/v1",
                property_id="level",
                value=StateValue(Quantity(0.0, "V")),
            )
        ],
    )
    projection = project_instrument_state(
        baseline,
        InstrumentStateCommand(
            command_id="set-level",
            instrument_id="source-0",
            assignments=[
                InstrumentStateAssignment(
                    resource_id="source-0",
                    target=state_member_target(level),
                    value=StateValue(Quantity(1_000.0, "mV")),
                )
            ],
        ),
        description=description,
    )
    command = CollectCommand(
        command_id="collect-level",
        instrument_id="source-0",
        point_index=0,
        point_count=1,
        requests=[
            CollectResultRequest(
                id="reading",
                interface_id="test.level/v1",
                acquisition_id="sample",
                result_id="reading",
                unit="V",
            )
        ],
    )

    assert (
        validate_collect_plan(
            command=command,
            description=description,
            baseline=projection,
        )
        == []
    )


def test_precondition_literals_have_canonical_contract_fingerprints() -> None:
    level = InterfaceRef("test.level/v1").property("level")

    def describe(
        value: float | Quantity,
        property_spec: PropertySpec,
    ) -> InstrumentDescription:
        return InstrumentDescription(
            instrument_id="source-0",
            implementation_id="tests.canonical_precondition",
            implementation_version="v1",
            interfaces=[
                interface(
                    level.interface_id,
                    properties=[property_spec],
                    acquisitions=[
                        acquisition(
                            "sample",
                            results=[acquisition_result("reading")],
                            preconditions=[
                                acquisition_precondition(
                                    level,
                                    value=value,
                                    unavailable_reason="Level differs.",
                                )
                            ],
                        )
                    ],
                )
            ],
        )

    integer_float = describe(1, float_property("level"))
    explicit_float = describe(1.0, float_property("level"))
    negative_zero = describe(
        Quantity(-0.0, "V"),
        quantity_property("level", unit="V"),
    )
    positive_zero = describe(
        Quantity(0.0, "V"),
        quantity_property("level", unit="V"),
    )

    assert integer_float.model_dump_json() == explicit_float.model_dump_json()
    assert negative_zero.model_dump_json() == positive_zero.model_dump_json()
    assert instrument_contract_fingerprint(
        "tests",
        (integer_float,),
    ) == instrument_contract_fingerprint("tests", (explicit_float,))
    assert instrument_contract_fingerprint(
        "tests",
        (negative_zero,),
    ) == instrument_contract_fingerprint("tests", (positive_zero,))


def test_description_normalization_owns_shared_interface_models() -> None:
    level = InterfaceRef("test.shared_level/v1").property("level")
    shared_acquisition = acquisition(
        "sample",
        results=[acquisition_result("reading")],
        preconditions=[
            acquisition_precondition(
                level,
                value=1,
                unavailable_reason="Level differs.",
            )
        ],
    )
    integer_interface = interface(
        level.interface_id,
        properties=[int_property("level")],
        acquisitions=[shared_acquisition],
    )
    float_interface = interface(
        level.interface_id,
        properties=[float_property("level")],
        acquisitions=[shared_acquisition],
    )
    integer_description = InstrumentDescription(
        instrument_id="integer-source",
        implementation_id="tests.shared_precondition",
        implementation_version="v1",
        interfaces=[integer_interface],
    )
    original_fingerprint = instrument_contract_fingerprint(
        "tests",
        (integer_description,),
    )

    float_description = InstrumentDescription(
        instrument_id="float-source",
        implementation_id="tests.shared_precondition",
        implementation_version="v1",
        interfaces=[float_interface],
    )

    assert (
        instrument_contract_fingerprint(
            "tests",
            (integer_description,),
        )
        == original_fingerprint
    )
    assert type(shared_acquisition.preconditions[0].value) is int
    assert (
        type(integer_description.interfaces[0].acquisitions[0].preconditions[0].value)
        is int
    )
    assert (
        type(float_description.interfaces[0].acquisitions[0].preconditions[0].value)
        is float
    )
    assert (
        InstrumentDescription.model_validate_json(integer_description.model_dump_json())
        == integer_description
    )


def test_instrument_ints_are_bounded_by_the_json_safe_range() -> None:
    safe_limit = (1 << 53) - 1
    count = InterfaceRef("test.counter/v1").property("count")
    property_spec = int_property("count")
    assert isinstance(property_spec.value_type.atom, Int)
    assert property_spec.value_type.atom.minimum == -safe_limit
    assert property_spec.value_type.atom.maximum == safe_limit

    with pytest.raises(
        ValidationError,
        match="JSON safe integer range",
    ):
        int_property("count", maximum=safe_limit + 1)

    with pytest.raises(
        ValidationError,
        match="at most",
    ):
        InstrumentDescription(
            instrument_id="counter-0",
            implementation_id="tests.counter",
            implementation_version="v1",
            interfaces=[
                interface(
                    count.interface_id,
                    properties=[property_spec],
                    acquisitions=[
                        acquisition(
                            "sample",
                            results=[acquisition_result("reading")],
                            preconditions=[
                                acquisition_precondition(
                                    count,
                                    value=safe_limit + 1,
                                    unavailable_reason="Count differs.",
                                )
                            ],
                        )
                    ],
                )
            ],
        )


def test_numeric_property_bounds_have_canonical_interface_wire() -> None:
    negative_zero = InstrumentDescription(
        instrument_id="source-negative",
        implementation_id="tests.quantity_bounds",
        implementation_version="v1",
        interfaces=[
            interface(
                "test.quantity_bounds/v1",
                properties=[quantity_property("level", unit="V", minimum=-0.0)],
            )
        ],
    )
    positive_zero = InstrumentDescription(
        instrument_id="source-positive",
        implementation_id="tests.quantity_bounds",
        implementation_version="v1",
        interfaces=[
            interface(
                "test.quantity_bounds/v1",
                properties=[quantity_property("level", unit="V", minimum=0.0)],
            )
        ],
    )

    assert (
        negative_zero.interfaces[0].model_dump_json()
        == positive_zero.interfaces[0].model_dump_json()
    )
    validate_instrument_description_collection((negative_zero, positive_zero))


def test_state_command_rejects_duplicate_physical_targets() -> None:
    with pytest.raises(
        ValidationError,
        match="instrument state command property targets must be unique",
    ):
        InstrumentStateCommand(
            command_id="duplicate-physical-target",
            instrument_id="source-0",
            assignments=[
                InstrumentStateAssignment(
                    resource_id="source-0",
                    target=state_member_target(
                        PropertyRef("test.dc/v1", (), "output_enabled")
                    ),
                    value=StateValue(False),
                    entity_ids=["channel-a"],
                ),
                InstrumentStateAssignment(
                    resource_id="source-0",
                    target=state_member_target(
                        PropertyRef("test.dc/v1", (), "output_enabled")
                    ),
                    value=StateValue(False),
                    entity_ids=["channel-b"],
                ),
            ],
        )


def test_state_command_accepts_same_property_on_distinct_routed_channels() -> None:
    command = InstrumentStateCommand.model_validate(
        {
            "command_id": "two-channel-state",
            "instrument_id": "source-0",
            "assignments": [
                {
                    "resource_id": "source-0",
                    "target": {
                        "kind": "interface",
                        "interface_id": "test.dc/v1",
                        "component_path": ["channels", channel_id],
                        "property_id": "output_enabled",
                    },
                    "value": False,
                    "entity_ids": [entity_id],
                    "channel_bindings": [
                        {
                            "entity_id": entity_id,
                            "channel_id": channel_id,
                            "interface_id": "test.dc/v1",
                        }
                    ],
                }
                for entity_id, channel_id in (
                    ("q0", "source.ch1"),
                    ("q1", "source.ch2"),
                )
            ],
        }
    )

    assert [
        assignment.channel_bindings[0].channel_id for assignment in command.assignments
    ] == ["source.ch1", "source.ch2"]
    patch = lower_state_patch(lower_backend_apply_request(command))
    assert patch.values == {}
    assert [entry.channel_bindings[0].channel_id for entry in patch.entries] == [
        "source.ch1",
        "source.ch2",
    ]


def test_instrument_state_observation_accepts_a_routed_channel_scope() -> None:
    state = InstrumentStateObservation.model_validate(
        {
            "target": {
                "kind": "interface",
                "interface_id": "test.control/v1",
                "component_path": [],
                "property_id": "gain",
            },
            "value": 1.0,
            "entity_ids": ["logical-channel"],
            "channel_bindings": [
                {
                    "entity_id": "logical-channel",
                    "channel_id": "output.ch1",
                    "interface_id": "test.control/v1",
                }
            ],
        }
    )

    assert state.channel_bindings[0].channel_id == "output.ch1"


def test_state_capture_requires_every_configured_capture_member() -> None:
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.static_state",
        implementation_version="v1",
        interfaces=[
            interface(
                "test.control/v1",
                properties=[
                    float_property("gain"),
                    string_property("secret", access="write_only"),
                ],
                components=[
                    component(
                        "channel-a",
                        properties=[
                            bool_property("enabled", access="read_only"),
                        ],
                    )
                ],
            )
        ],
    )
    empty = InstrumentStateSnapshot(instrument_id="source-0")

    assert validate_state_snapshot(snapshot=empty, description=description) == []
    assert [
        item.code
        for item in validate_state_capture(
            snapshot=empty,
            description=description,
            required=capture_state_members(description),
        )
    ] == ["instrument_driver_snapshot_missing_members"]

    complete = InstrumentStateSnapshot(
        instrument_id="source-0",
        observations=[
            _observation(
                interface_id="test.control/v1",
                property_id="gain",
                value=StateValue(1.0),
            ),
            _observation(
                interface_id="test.control/v1",
                component_path=["channel-a"],
                property_id="enabled",
                value=StateValue(True),
            ),
        ],
    )
    assert (
        validate_state_snapshot(
            snapshot=complete,
            description=description,
        )
        == []
    )

    with_write_only = complete.model_copy(
        update={
            "observations": [
                *complete.observations,
                _observation(
                    interface_id="test.control/v1",
                    property_id="secret",
                    value=StateValue("returned-secret"),
                ),
            ]
        }
    )
    assert [
        item.code
        for item in validate_state_snapshot(
            snapshot=with_write_only,
            description=description,
        )
    ] == ["instrument_driver_snapshot_write_only_property"]


def test_interface_mounts_separate_capability_contract_from_physical_paths() -> None:
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.mounted_state",
        implementation_version="v1",
        components=[
            instrument_component(
                "channels",
                components=[
                    instrument_component("ch1"),
                    instrument_component("ch2"),
                ],
            )
        ],
        interfaces=[
            interface(
                "test.control/v1",
                properties=[float_property("gain")],
            )
        ],
        interface_mounts=[
            interface_mount("test.control/v1", "channels", "ch1"),
            interface_mount("test.control/v1", "channels", "ch2"),
        ],
    )
    command = InstrumentStateCommand(
        command_id="mounted-state",
        instrument_id="source-0",
        assignments=[
            InstrumentStateAssignment(
                resource_id="source-0",
                target=state_member_target(
                    PropertyRef(
                        "test.control/v1",
                        ("channels", "ch1"),
                        "gain",
                    )
                ),
                value=StateValue(1.0),
            )
        ],
    )

    assert validate_state_command(command=command, description=description) == []
    root_assignment = command.assignments[0].model_copy(
        update={
            "target": state_member_target(PropertyRef("test.control/v1", (), "gain"))
        }
    )
    root_command = command.model_copy(update={"assignments": [root_assignment]})
    assert [
        problem.code
        for problem in validate_state_command(
            command=root_command,
            description=description,
        )
    ] == ["instrument_driver_unsupported_member"]

    complete = InstrumentStateSnapshot(
        instrument_id="source-0",
        observations=[
            _observation(
                interface_id="test.control/v1",
                component_path=["channels", channel_id],
                property_id="gain",
                value=StateValue(1.0),
            )
            for channel_id in ("ch1", "ch2")
        ],
    )
    assert validate_state_snapshot(snapshot=complete, description=description) == []


def test_interface_mounts_require_distinct_declared_physical_components() -> None:
    with pytest.raises(
        ValidationError,
        match="mount references unknown instrument component 'channels/ch2'",
    ):
        InstrumentDescription(
            instrument_id="source-0",
            implementation_id="tests.missing_mount_component",
            implementation_version="v1",
            components=[
                instrument_component(
                    "channels",
                    components=[instrument_component("ch1")],
                )
            ],
            interfaces=[interface("test.control/v1")],
            interface_mounts=[interface_mount("test.control/v1", "channels", "ch2")],
        )

    with pytest.raises(ValidationError, match="mounts must not overlap"):
        InstrumentDescription(
            instrument_id="source-0",
            implementation_id="tests.overlapping_mounts",
            implementation_version="v1",
            components=[
                instrument_component(
                    "channels",
                    components=[
                        instrument_component(
                            "ch1",
                            components=[instrument_component("nested")],
                        )
                    ],
                )
            ],
            interfaces=[interface("test.control/v1")],
            interface_mounts=[
                interface_mount("test.control/v1", "channels", "ch1"),
                interface_mount(
                    "test.control/v1",
                    "channels",
                    "ch1",
                    "nested",
                ),
            ],
        )


def test_mounted_acquisition_references_state_on_the_same_physical_component() -> None:
    control = InterfaceRef("test.control/v1")
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.mounted_acquisition",
        implementation_version="v1",
        components=[
            instrument_component(
                "channels",
                components=[
                    instrument_component("ch1"),
                    instrument_component("ch2"),
                ],
            )
        ],
        interfaces=[
            interface(
                control.interface_id,
                properties=[
                    bool_property("enabled"),
                    int_property("points", minimum=1),
                ],
            ),
            interface(
                "test.monitor/v1",
                acquisitions=[
                    acquisition(
                        "sample",
                        preconditions=[
                            acquisition_precondition(
                                control.property("enabled"),
                                value=True,
                                unavailable_reason="Channel is disabled.",
                            )
                        ],
                        results=[
                            acquisition_result(
                                "trace",
                                axes=[
                                    acquisition_axis(
                                        "sample",
                                        size=control.property("points"),
                                    )
                                ],
                            )
                        ],
                    )
                ],
            ),
        ],
        interface_mounts=[
            interface_mount(interface_id, "channels", channel_id)
            for interface_id in ("test.control/v1", "test.monitor/v1")
            for channel_id in ("ch1", "ch2")
        ],
    )
    state = InstrumentStateSnapshot(
        instrument_id="source-0",
        observations=[
            _observation(
                interface_id="test.control/v1",
                component_path=["channels", channel_id],
                property_id=property_id,
                value=StateValue(value),
            )
            for channel_id, enabled, points in (
                ("ch1", False, 2),
                ("ch2", True, 5),
            )
            for property_id, value in (
                ("enabled", enabled),
                ("points", points),
            )
        ],
    )

    selected = resolve_interactive_collect(
        intent=InteractiveCollectIntent(
            command_id="mounted-collect",
            instrument_id="source-0",
            interface_id="test.monitor/v1",
            component_path=["channels", "ch2"],
            acquisition_id="sample",
        ),
        description=description,
        state=state,
    )
    blocked = resolve_interactive_collect(
        intent=InteractiveCollectIntent(
            command_id="mounted-collect-blocked",
            instrument_id="source-0",
            interface_id="test.monitor/v1",
            component_path=["channels", "ch1"],
            acquisition_id="sample",
        ),
        description=description,
        state=state,
    )

    assert isinstance(selected, ResolvedInteractiveCollect)
    assert selected.command.requests[0].dimensions[0].size == 5
    assert isinstance(blocked, RejectedInteractiveCollect)
    assert [problem.code for problem in blocked.problems] == [
        "instrument_driver_acquisition_precondition_not_met"
    ]
    [related] = blocked.problems[0].related_locations
    assert isinstance(related, ModelLocation)
    assert related.path == (
        "test.control/v1",
        "channels",
        "ch1",
        "enabled",
    )


def test_state_snapshot_requires_declared_quantity_units() -> None:
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.canonical_snapshot_units",
        implementation_version="v1",
        interfaces=[
            interface(
                "test.source/v1",
                properties=[quantity_property("frequency", unit="GHz")],
            )
        ],
    )
    snapshot = InstrumentStateSnapshot(
        instrument_id="source-0",
        observations=[
            _observation(
                interface_id="test.source/v1",
                property_id="frequency",
                value=StateValue(Quantity(5_000.0, "MHz")),
            )
        ],
    )

    [problem] = validate_state_snapshot(
        snapshot=snapshot,
        description=description,
    )
    assert problem.code == "instrument_driver_snapshot_property_value_mismatch"
    assert isinstance(problem.location, ModelLocation)
    assert problem.location.path == ("observations", 0, "value", "unit")


def test_member_projection_overlays_flat_observable_properties() -> None:
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.flat_projection",
        implementation_version="v1",
        interfaces=[
            interface(
                "test.control/v1",
                properties=[
                    float_property("gain"),
                    string_property("secret", access="write_only"),
                ],
            )
        ],
    )
    observed = InstrumentStateSnapshot(
        instrument_id="source-0",
        observations=[
            _observation(
                interface_id="test.control/v1",
                property_id="gain",
                value=StateValue(1.0),
            )
        ],
    )
    command = InstrumentStateCommand(
        command_id="set-control",
        instrument_id="source-0",
        assignments=[
            InstrumentStateAssignment(
                resource_id="source-0",
                target=state_member_target(PropertyRef("test.control/v1", (), "gain")),
                value=StateValue(2.0),
            ),
            InstrumentStateAssignment(
                resource_id="source-0",
                target=state_member_target(
                    PropertyRef("test.control/v1", (), "secret")
                ),
                value=StateValue("updated"),
            ),
        ],
    )

    projected = project_instrument_state(
        observed,
        command,
        description=description,
    )
    assert [
        (item.target.property_id, item.value.root) for item in projected.observations
    ] == [("gain", 2.0)]
    observed = InstrumentStateSnapshot(
        instrument_id=projected.instrument_id,
        observations=list(projected.observations),
    )
    assert validate_state_snapshot(snapshot=observed, description=description) == []


def _interactive_collect_intent(
    *,
    command_id: str = "collect-signal",
    instrument_id: str = "source-0",
    interface_id: str = "test.trace/v1",
    component_path: tuple[str, ...] = (),
    acquisition_id: str = "sample",
    result_ids: tuple[str, ...] = (),
) -> InteractiveCollectIntent:
    return InteractiveCollectIntent(
        command_id=command_id,
        instrument_id=instrument_id,
        interface_id=interface_id,
        component_path=list(component_path),
        acquisition_id=acquisition_id,
        result_ids=list(result_ids),
    )


def _interactive_monitor_intent(
    *,
    command_id: str,
    result_ids: tuple[str, ...] = (),
) -> InteractiveCollectIntent:
    return InteractiveCollectIntent(
        command_id=command_id,
        instrument_id="source-0",
        interface_id="test.monitor/v1",
        acquisition_id="monitor",
        result_ids=list(result_ids),
    )


def test_acquisition_result_rejects_duplicate_axis_ids() -> None:
    with pytest.raises(
        ValidationError,
        match="acquisition result axis ids must be unique",
    ):
        acquisition_result(
            "signal",
            axes=[
                acquisition_axis("frequency", size=3, kind="frequency"),
                acquisition_axis("frequency", size=3, kind="frequency"),
            ],
        )


def test_interactive_collect_intent_requires_unique_result_ids() -> None:
    with pytest.raises(
        ValidationError,
        match="interactive collect result ids must be unique",
    ):
        InteractiveCollectIntent(
            command_id="collect-duplicate",
            instrument_id="source-0",
            interface_id="test.trace/v1",
            acquisition_id="sample",
            result_ids=["signal", "signal"],
        )


def test_interactive_collect_resolves_all_or_explicit_fixed_results() -> None:
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.interactive_collect",
        implementation_version="v1",
        interfaces=[
            interface(
                "test.trace/v1",
                acquisitions=[
                    acquisition(
                        "sample",
                        results=[
                            acquisition_result("signal", unit="V"),
                            acquisition_result("phase", unit="rad"),
                        ],
                    )
                ],
            )
        ],
    )
    state = InstrumentStateSnapshot(instrument_id="source-0")

    all_results = resolve_interactive_collect(
        intent=_interactive_collect_intent(command_id="collect-all"),
        description=description,
        state=state,
    )
    explicit = resolve_interactive_collect(
        intent=_interactive_collect_intent(
            command_id="collect-phase",
            result_ids=("phase",),
        ),
        description=description,
        state=state,
    )

    assert isinstance(all_results, ResolvedInteractiveCollect)
    assert all_results.command.point_index == 0
    assert all_results.command.point_count == 1
    assert [
        (request.id, request.result_id, request.unit)
        for request in all_results.command.requests
    ] == [
        ("signal", "signal", "V"),
        ("phase", "phase", "rad"),
    ]
    assert isinstance(explicit, ResolvedInteractiveCollect)
    assert [request.result_id for request in explicit.command.requests] == ["phase"]
    assert (
        validate_collect_plan(
            command=explicit.command,
            description=description,
            baseline=state,
        )
        == []
    )


@pytest.mark.parametrize(
    ("intent", "code", "path"),
    [
        (
            _interactive_collect_intent(instrument_id="other"),
            "instrument_driver_mismatch",
            ("instrument_id",),
        ),
        (
            _interactive_collect_intent(interface_id="test.missing/v1"),
            "instrument_driver_unsupported_interface",
            ("interface_id",),
        ),
        (
            _interactive_collect_intent(component_path=("missing",)),
            "instrument_driver_unsupported_component",
            ("component_path",),
        ),
        (
            _interactive_collect_intent(acquisition_id="missing"),
            "instrument_driver_unsupported_acquisition",
            ("acquisition_id",),
        ),
        (
            _interactive_collect_intent(result_ids=("missing",)),
            "instrument_driver_unsupported_acquisition_result",
            ("result_ids", 0),
        ),
    ],
    ids=("instrument", "interface", "component", "acquisition", "result"),
)
def test_interactive_collect_rejects_unsupported_targets(
    intent: InteractiveCollectIntent,
    code: str,
    path: tuple[str | int, ...],
) -> None:
    resolution = resolve_interactive_collect(
        intent=intent,
        description=_collect_description(),
        state=InstrumentStateSnapshot(instrument_id="source-0"),
    )

    assert isinstance(resolution, RejectedInteractiveCollect)
    [problem] = resolution.problems
    assert problem.code == code
    assert problem.location == ModelLocation(
        root="interactive_collect_intent",
        path=path,
    )


def test_interactive_collect_reports_blocked_preconditions() -> None:
    blocked_description = _monitor_collect_description(
        preconditions=(
            acquisition_precondition(
                InterfaceRef("test.source/v1")
                .component("output")
                .property("output_enabled"),
                value=True,
                unavailable_reason="Source output is disabled.",
            ),
        )
    )
    blocked = resolve_interactive_collect(
        intent=_interactive_monitor_intent(command_id="collect-blocked"),
        description=blocked_description,
        state=_monitor_collect_snapshot(),
    )

    assert isinstance(blocked, RejectedInteractiveCollect)
    assert [problem.code for problem in blocked.problems] == [
        "instrument_driver_acquisition_precondition_not_met"
    ]
    assert blocked.problems[0].related_locations == (
        ModelLocation(
            root="instrument_state",
            path=("test.source/v1", "output", "output_enabled"),
        ),
    )


def test_interactive_collect_freezes_state_sized_axes() -> None:
    description = _state_sized_collect_description()
    resolved = resolve_interactive_collect(
        intent=_interactive_collect_intent(command_id="collect-sized"),
        description=description,
        state=_state_sized_collect_snapshot(17),
    )
    unresolved = resolve_interactive_collect(
        intent=_interactive_collect_intent(command_id="collect-unsized"),
        description=description,
        state=InstrumentStateSnapshot(instrument_id="other"),
    )

    assert isinstance(resolved, ResolvedInteractiveCollect)
    assert resolved.command.requests[0].dimensions == [
        CollectAxisRequest(id="channel", kind="channel", size=2),
        CollectAxisRequest(
            id="frequency",
            kind="frequency",
            size=17,
            unit="Hz",
        ),
    ]
    assert isinstance(unresolved, RejectedInteractiveCollect)
    [problem] = unresolved.problems
    assert problem.code == "instrument_driver_acquisition_axis_state_unknown"
    assert problem.location == ModelLocation(
        root="interactive_collect_intent",
        path=("acquisition_id",),
    )
    assert problem.related_locations == (
        ModelLocation(
            root="instrument_state",
            path=("test.sweep/v1", "points"),
        ),
    )
    assert problem.details == {
        "result_id": "signal",
        "axis_id": "frequency",
        "axis_index": 1,
        "state_property": {
            "interface_id": "test.sweep/v1",
            "component_path": (),
            "property_id": "points",
        },
    }


def test_acquisition_axis_requires_an_explicit_size_contract() -> None:
    serialized = _collect_description().model_dump(mode="json")
    del serialized["interfaces"][0]["acquisitions"][0]["results"][0]["axes"][0]["size"]

    with pytest.raises(ValidationError, match="Field required"):
        InstrumentDescription.model_validate(serialized)


def test_state_sized_axis_has_canonical_wire_and_fingerprint() -> None:
    description = _state_sized_collect_description()
    result = description.interfaces[1].acquisitions[0].results[0]
    axis = result.axes[1]
    serialized = description.model_dump(mode="json")
    restored = InstrumentDescription.model_validate(serialized)

    assert isinstance(axis.size, StatePropertyRef)
    assert serialized["interfaces"][1]["acquisitions"][0]["results"][0]["axes"][1][
        "size"
    ] == {
        "interface_id": "test.sweep/v1",
        "component_path": [],
        "property_id": "points",
    }
    assert instrument_contract_fingerprint(
        "tests",
        (description,),
    ) == instrument_contract_fingerprint("tests", (restored,))


@pytest.mark.parametrize(
    ("property_spec", "size_reference", "message"),
    [
        (
            int_property("points", minimum=1),
            InterfaceRef("test.sweep/v1").property("missing"),
            "size must reference a declared property",
        ),
        (
            int_property("points", minimum=1, access="write_only"),
            InterfaceRef("test.sweep/v1").property("points"),
            "size property must be observable",
        ),
        (
            float_property("points"),
            InterfaceRef("test.sweep/v1").property("points"),
            "size property must be an integer",
        ),
        (
            int_property("points", minimum=0),
            InterfaceRef("test.sweep/v1").property("points"),
            "size property must have a positive minimum",
        ),
    ],
    ids=("missing", "write-only", "non-integer", "non-positive-minimum"),
)
def test_state_sized_axis_rejects_invalid_property_references(
    property_spec: PropertySpec,
    size_reference: PropertyRef,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _state_sized_collect_description(
            property_spec=property_spec,
            size_reference=size_reference,
        )


def test_mixed_fixed_and_state_sized_axes_resolve_from_one_snapshot() -> None:
    description = _state_sized_collect_description()
    result = description.interfaces[1].acquisitions[0].results[0]
    command = _state_sized_collect_command(17)

    assert (
        resolve_acquisition_dimensions(
            description=description,
            result=result,
            state=None,
        )
        is None
    )
    assert resolve_acquisition_dimensions(
        description=description,
        result=result,
        state=_state_sized_collect_snapshot(17),
    ) == tuple(command.requests[0].dimensions)
    assert validate_collect_command(command=command, description=description) == []


def test_collect_plan_checks_state_sized_axes_at_the_exact_state_target() -> None:
    description = _state_sized_collect_description()
    command = _state_sized_collect_command(17)

    [unknown] = validate_collect_plan(
        command=command,
        description=description,
        baseline=None,
    )
    matching = validate_collect_plan(
        command=command,
        description=description,
        baseline=_state_sized_collect_snapshot(17),
    )
    [mismatch] = validate_collect_plan(
        command=command,
        description=description,
        baseline=_state_sized_collect_snapshot(19),
    )

    location = ModelLocation(
        root="instrument_collect_command",
        path=("requests", "signal", "dimensions", 1, "size"),
    )
    related = (
        ModelLocation(
            root="instrument_state",
            path=("test.sweep/v1", "points"),
        ),
    )
    assert unknown.code == "instrument_driver_acquisition_axis_state_unknown"
    assert unknown.location == location
    assert unknown.related_locations == related
    assert unknown.details == {
        "state_property": {
            "interface_id": "test.sweep/v1",
            "component_path": (),
            "property_id": "points",
        },
        "requested_size": 17,
    }
    assert matching == []
    assert mismatch.code == "instrument_driver_acquisition_axis_state_mismatch"
    assert mismatch.location == location
    assert mismatch.related_locations == related
    assert mismatch.details == {
        **unknown.details,
        "observed_value": 19,
    }


def test_collect_validator_reports_unsupported_result_without_crashing() -> None:
    problems = validate_collect_command(
        command=CollectCommand(
            command_id="unsupported-result",
            instrument_id="source-0",
            point_index=0,
            point_count=1,
            requests=[
                CollectResultRequest(
                    id="missing",
                    interface_id="test.trace/v1",
                    acquisition_id="sample",
                    result_id="missing",
                )
            ],
        ),
        description=_collect_description(),
    )

    assert [item.code for item in problems] == [
        "instrument_driver_unsupported_acquisition_result"
    ]


def test_collect_validator_accepts_compatible_units_and_fixed_shapes() -> None:
    problems = validate_collect_command(
        command=_collect_command(
            unit="GHz",
            dimensions=[
                CollectAxisRequest(
                    id="frequency",
                    kind="frequency",
                    size=17,
                    unit="MHz",
                )
            ],
        ),
        description=_collect_description(),
    )

    assert problems == []


def test_collect_validator_requires_every_declared_axis() -> None:
    [problem] = validate_collect_command(
        command=_collect_command(unit=None, dimensions=[]),
        description=_collect_description(),
    )

    assert problem.code == "instrument_driver_acquisition_axes_mismatch"


def test_collect_validator_checks_dtype_unit_and_axis_contracts() -> None:
    problems = validate_collect_command(
        command=_collect_command(
            dtype="int64",
            unit="K",
            dimensions=[
                CollectAxisRequest(
                    id="time",
                    kind="time",
                    size=3,
                    unit="K",
                )
            ],
        ),
        description=_collect_description(),
    )

    assert {item.code for item in problems} == {
        "instrument_driver_acquisition_dtype_mismatch",
        "instrument_driver_acquisition_unit_mismatch",
        "instrument_driver_acquisition_axis_mismatch",
        "instrument_driver_acquisition_axis_unit_mismatch",
    }


def test_collect_receipt_validator_checks_results_and_value_contract() -> None:
    command = _collect_command(
        dimensions=[
            CollectAxisRequest(
                id="frequency",
                kind="frequency",
                size=2,
                unit="Hz",
            )
        ]
    )
    valid = validate_collect_receipt(
        command=command,
        receipt=CollectReceipt(
            readback=RecordInstrumentReadback(
                values={
                    "signal": MeasurementArray.create(
                        dtype="float64",
                        unit="Hz",
                        values=(1.0, 2.0),
                    )
                }
            )
        ),
    )
    invalid = validate_collect_receipt(
        command=command,
        receipt=CollectReceipt(
            readback=RecordInstrumentReadback(
                values={
                    "unexpected": MeasurementArray.create(
                        dtype="string",
                        values=("bad",),
                    )
                }
            )
        ),
    )
    mismatched_value = validate_collect_receipt(
        command=command,
        receipt=CollectReceipt(
            readback=RecordInstrumentReadback(
                values={
                    "signal": MeasurementArray.create(
                        dtype="string",
                        values=("bad",),
                    )
                }
            )
        ),
    )
    convertible_unit_mismatch = validate_collect_receipt(
        command=command,
        receipt=CollectReceipt(
            readback=RecordInstrumentReadback(
                values={
                    "signal": MeasurementArray.create(
                        dtype="float64",
                        unit="GHz",
                        values=(1.0, 2.0),
                    )
                }
            )
        ),
    )

    assert valid == []
    assert {item.code for item in invalid} == {
        "instrument_driver_missing_acquisition_result",
        "instrument_driver_unexpected_acquisition_result",
    }
    assert {item.code for item in mismatched_value} == {
        "instrument_driver_readback_dtype_mismatch",
        "instrument_driver_readback_unit_mismatch",
        "instrument_driver_readback_shape_mismatch",
    }
    assert [item.code for item in convertible_unit_mismatch] == [
        "instrument_driver_readback_unit_mismatch"
    ]


@pytest.mark.parametrize(
    "value",
    (
        MeasurementArray.create(
            dtype="float64",
            unit="Hz",
            values=(1.0, 2.0, 3.0),
        ),
        MeasurementUnavailable.create(
            reason="overload",
            dtype="float64",
            unit="Hz",
            shape=(3,),
            metadata={"instrument_status": "adc overload"},
        ),
    ),
    ids=("available", "unavailable"),
)
def test_collect_receipt_enforces_the_concrete_requested_shape(
    value: MeasurementArray | MeasurementUnavailable,
) -> None:
    def validate(size: int) -> list[Problem]:
        return validate_collect_receipt(
            command=_collect_command(
                dimensions=[
                    CollectAxisRequest(
                        id="frequency",
                        kind="frequency",
                        size=size,
                        unit="Hz",
                    )
                ]
            ),
            receipt=CollectReceipt(
                readback=RecordInstrumentReadback(values={"signal": value})
            ),
        )

    assert validate(3) == []
    assert [problem.code for problem in validate(2)] == [
        "instrument_driver_readback_shape_mismatch"
    ]


def _state_command(
    *,
    interface_id: str,
    property_id: str,
    value: StateValue,
) -> InstrumentStateCommand:
    return InstrumentStateCommand(
        command_id="state-command",
        instrument_id="source-0",
        assignments=[
            InstrumentStateAssignment(
                resource_id="source-0",
                target=state_member_target(PropertyRef(interface_id, (), property_id)),
                value=value,
            )
        ],
    )


def _collect_description() -> InstrumentDescription:
    return InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.collect_driver",
        implementation_version="v0",
        interfaces=[
            interface(
                "test.trace/v1",
                acquisitions=[
                    acquisition(
                        "sample",
                        results=[
                            acquisition_result(
                                "signal",
                                dtype="float64",
                                unit="Hz",
                                axes=[
                                    acquisition_axis(
                                        "frequency",
                                        size=17,
                                        kind="frequency",
                                        unit="Hz",
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
    )


def _state_sized_collect_description(
    *,
    property_spec: PropertySpec | None = None,
    size_reference: PropertyRef | None = None,
) -> InstrumentDescription:
    sweep = InterfaceRef("test.sweep/v1")
    return InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.state_sized_collect",
        implementation_version="v1",
        interfaces=[
            interface(
                sweep.interface_id,
                properties=[
                    property_spec or int_property("points", minimum=1),
                ],
            ),
            interface(
                "test.trace/v1",
                acquisitions=[
                    acquisition(
                        "sample",
                        results=[
                            acquisition_result(
                                "signal",
                                unit="Hz",
                                axes=[
                                    acquisition_axis(
                                        "channel",
                                        size=2,
                                    ),
                                    acquisition_axis(
                                        "frequency",
                                        size=size_reference or sweep.property("points"),
                                        kind="frequency",
                                        unit="Hz",
                                    ),
                                ],
                            )
                        ],
                    )
                ],
            ),
        ],
    )


def _state_sized_collect_snapshot(points: int) -> InstrumentStateSnapshot:
    return InstrumentStateSnapshot(
        instrument_id="source-0",
        observations=[
            _observation(
                interface_id="test.sweep/v1",
                property_id="points",
                value=StateValue(points),
            )
        ],
    )


def _state_sized_collect_command(points: int) -> CollectCommand:
    return _collect_command(
        dimensions=[
            CollectAxisRequest(
                id="channel",
                kind="channel",
                size=2,
            ),
            CollectAxisRequest(
                id="frequency",
                kind="frequency",
                size=points,
                unit="Hz",
            ),
        ]
    )


def _monitor_collect_description(
    *,
    preconditions: tuple[AcquisitionPreconditionSpec, ...] = (),
) -> InstrumentDescription:
    return InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.monitor_collect",
        implementation_version="v1",
        interfaces=[
            interface(
                "test.source/v1",
                components=[
                    component(
                        "output",
                        properties=[bool_property("output_enabled")],
                    )
                ],
            ),
            interface(
                "test.monitor/v1",
                acquisitions=[
                    acquisition(
                        "monitor",
                        results=[
                            acquisition_result(
                                "monitored_current",
                                unit="A",
                            )
                        ],
                        preconditions=preconditions,
                    )
                ],
            ),
        ],
    )


def _monitor_collect_snapshot(
    *, output_enabled: bool = False
) -> InstrumentStateSnapshot:
    return InstrumentStateSnapshot(
        instrument_id="source-0",
        observations=[
            _observation(
                interface_id="test.source/v1",
                component_path=["output"],
                property_id="output_enabled",
                value=StateValue(output_enabled),
            ),
        ],
    )


def _monitor_collect_command(
    result_id: str = "monitored_current",
    *,
    unit: str,
) -> CollectCommand:
    return CollectCommand(
        command_id=f"collect-{result_id}",
        instrument_id="source-0",
        point_index=0,
        point_count=1,
        requests=[
            CollectResultRequest(
                id=result_id,
                interface_id="test.monitor/v1",
                acquisition_id="monitor",
                result_id=result_id,
                unit=unit,
            )
        ],
    )


def _collect_command(
    *,
    dtype: MeasurementDType = "float64",
    unit: str | None = "Hz",
    dimensions: list[CollectAxisRequest],
) -> CollectCommand:
    return CollectCommand(
        command_id="collect-command",
        instrument_id="source-0",
        point_index=0,
        point_count=1,
        requests=[
            CollectResultRequest(
                id="signal",
                interface_id="test.trace/v1",
                acquisition_id="sample",
                result_id="signal",
                dtype=dtype,
                unit=unit,
                dimensions=dimensions,
            )
        ],
    )


@pytest.mark.parametrize(
    "source",
    [
        "hardware_query",
        "configured_fixed",
        "derived",
        "command_confirmed",
    ],
)
def test_write_only_confirmation_keeps_observable_and_restore_limits(
    source: ObservationSource,
) -> None:
    target = InterfaceRef("test.write_only/v1").property("frequency")
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="tests.write_only",
        implementation_version="1",
        interfaces=[
            interface(
                target.interface_id,
                properties=[
                    quantity_property("frequency", unit="GHz", access="write_only"),
                ],
            )
        ],
    )
    snapshot = InstrumentStateSnapshot(
        instrument_id="source-0",
        observations=[
            state_observation(target, StateValue(Quantity(5.1, "GHz")), source=source),
        ],
    )
    problems = validate_state_snapshot(snapshot=snapshot, description=description)
    assert [item.code for item in problems] == (
        []
        if source == "command_confirmed"
        else ["instrument_driver_snapshot_write_only_property"]
    )
    # Explicit confirmation does not make a write-only property observable.
    assert capture_state_members(description) == ()
    problems = validate_reconciled_state_assignments(
        instrument_id="source-0",
        description=description,
        baseline=snapshot if source == "command_confirmed" else None,
        assignments=[
            InstrumentStateAssignment(
                resource_id="source-0",
                target=state_member_target(target),
                value=StateValue(Quantity(5.1, "GHz")),
            )
        ],
    )
    assert [item.code for item in problems] == ["instrument_driver_write_only_property"]
    with pytest.raises(ValidationError, match="restorable"):
        quantity_property("frequency", unit="GHz", access="write_only", restore=True)
