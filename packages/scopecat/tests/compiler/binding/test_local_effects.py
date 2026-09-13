from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import Annotated

import pytest
from scopecat_testkit.authoring import bind_invocation, load_config
from scopecat_testkit.bound_program import (
    ComputeNodeFixture,
    bind_program_facts,
    compute_result,
    instrument_invocation,
    program_fixture,
)
from scopecat_testkit.expressions import state_property, verified_scalar_expr
from scopecat_testkit.local_materialization import (
    materialize_local_execution,
    operations_of_type,
)
from scopecat_testkit.materialized_effects import config_with_physical_resources
from scopecat_testkit.payload_codecs import json_payload_codecs

import scopecat as sc
from scopecat.compiler.bound_facts import (
    LogicalResourceRequirement,
)
from scopecat.compiler.point_domain import PointDomain
from scopecat.compiler.relations.verification import (
    ExpressionTypeBindings,
    ExpressionVerificationError,
    RowType,
)
from scopecat.config.environment import build_config_environment
from scopecat.execution.local.program import (
    ApplyStateOperation,
    BoundInput,
    ComputeOperation,
    InvokeOperation,
    OutputInput,
)
from scopecat.kernel.content_identity import content_fingerprint
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.resource_identity import (
    LogicalResourcePortId,
    logical_resource_port_id,
)
from scopecat.kernel.state import PayloadRef
from scopecat.kernel.symbols import SymbolId
from scopecat.kernel.value_data import CellValue
from scopecat.kernel.value_types import (
    Bool,
    Float,
    Int,
    Payload,
    Scalar,
    String,
    Table,
    TableColumn,
)
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.program.expressions import (
    ComputeResultScalarExpr,
    lit,
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
from scopecat.sdk.instruments import InterfaceRef


class _FirstIntegerToken(IntEnum):
    ONE = 1


class _SecondIntegerToken(IntEnum):
    ONE = 1


class _TextToken(StrEnum):
    ONE = "one"


def _operation_id(local_id: str) -> OperationId:
    return OperationId(SymbolId(local_id=local_id))


def _output(
    operation_id: OperationId,
    value_type: Scalar,
    *,
    value_id: ValueId | None = None,
) -> ComputeOutput:
    return ComputeOutput(
        id=value_id or operation_result_id(operation_id),
        value_type=value_type,
    )


def _implementation(
    operation_id: OperationId,
    kernel: Callable[..., object],
) -> LocalPythonImplementation:
    return LocalPythonImplementation(
        id=ImplementationId(f"python.{operation_id.qualified_name}.v1"),
        kernel=kernel,
    )


def _wrap_value(*, value: object) -> dict[str, object]:
    return {"value": value}


def _identity_value(*, value: object) -> object:
    return value


def _quantity_value(*, frequency: Quantity) -> float:
    return frequency.value


def _mapping_size(*, payload: Mapping[object, object]) -> float:
    return float(len(payload))


def _point_domain(
    rows: tuple[tuple[CellValue, ...], ...],
    value_type: Table,
) -> PointDomain:
    if not value_type.columns:
        return PointDomain(axes=())
    [column] = value_type.columns
    return PointDomain(
        axes=(
            point_axis_values(
                column.id,
                column.value_type,
                tuple(row[0] for row in rows),
            ),
        )
    )


def _point_bindings(value_type: Table) -> ExpressionTypeBindings:
    return ExpressionTypeBindings(point_row=RowType.from_table(value_type))


def _resource(value: str) -> LogicalResourcePortId:
    return logical_resource_port_id(value)


def test_content_fingerprint_preserves_primitive_enum_types() -> None:
    first = content_fingerprint(_FirstIntegerToken.ONE)

    assert first != content_fingerprint(1)
    assert first != content_fingerprint(_SecondIntegerToken.ONE)
    assert content_fingerprint(_TextToken.ONE) != content_fingerprint("one")


def test_content_fingerprint_normalizes_equivalent_decimals() -> None:
    assert content_fingerprint(Decimal("1.00")) == content_fingerprint(Decimal("1"))
    assert content_fingerprint(Decimal("-0")) == content_fingerprint(Decimal("0.0"))


@pytest.mark.parametrize(
    ("value", "value_type"),
    [
        (True, Scalar(Bool())),
        (3, Scalar(Int())),
        ("operate", Scalar(String())),
    ],
)
def test_bound_state_preserves_primitive_field_types(
    value: str | int | bool,
    value_type: Scalar,
) -> None:
    program = program_fixture(
        point_domain=_point_domain(
            ((),),
            Table(columns=()),
        ),
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=_resource("source-0"),
                capabilities=(InterfaceRef("test.configure/v1"),),
            ),
        ),
        state=(
            state_property(
                _resource("source-0"),
                interface_id="test.configure/v1",
                property_id="value",
                value=verified_scalar_expr(lit(value), expected_type=value_type),
            ),
        ),
    )
    environment = build_config_environment(
        config_with_physical_resources({"source-0": ("test.configure/v1",)})
    )

    plan = materialize_local_execution(bind_program_facts(program, environment))

    assert (
        operations_of_type(plan, ApplyStateOperation, point_index=0)[0]
        .targets[0]
        .value.root
        == value
    )
    assert type(
        operations_of_type(plan, ApplyStateOperation, point_index=0)[0]
        .targets[0]
        .value.root
    ) is type(value)


def test_effects_use_logical_point_and_point_local_payload_identity() -> None:
    producer_id = _operation_id("produce")
    consumer_id = _operation_id("consume")
    unused_id = _operation_id("a-unused-payload")
    producer_output_id = operation_result_id(producer_id)
    consumer_output_id = operation_result_id(consumer_id)
    point_type = Table(columns=(TableColumn("value", Scalar(Float())),))
    program = program_fixture(
        point_domain=_point_domain(
            ((1.0,), (1.0,), (2.0,)),
            point_type,
        ),
        compute_nodes=(
            ComputeNodeFixture(
                id=unused_id,
                implementation=_implementation(
                    unused_id,
                    lambda: {"unused": True},
                ),
                result=_output(unused_id, Scalar(Payload("unused_program"))),
                input_types={},
            ),
            ComputeNodeFixture(
                id=producer_id,
                implementation=_implementation(producer_id, _identity_value),
                inputs={
                    "value": verified_scalar_expr(
                        point_col("value", Scalar(Float())),
                        expected_type=Scalar(Float()),
                        bindings=_point_bindings(point_type),
                    )
                },
                result=_output(producer_id, Scalar(Float())),
                input_types={"value": Scalar(Float())},
            ),
            ComputeNodeFixture(
                id=consumer_id,
                implementation=_implementation(consumer_id, _wrap_value),
                inputs={
                    "value": ComputeResultScalarExpr(
                        value_id=producer_output_id,
                        value_type=Scalar(Float()),
                    )
                },
                result=_output(consumer_id, Scalar(Payload("pulse_program"))),
                input_types={"value": Scalar(Float())},
            ),
        ),
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=_resource("source-0"),
                capabilities=(InterfaceRef("test.play_program/v1"),),
            ),
        ),
        invocations=(
            instrument_invocation(
                id="play-program",
                resource_port_id=_resource("source-0"),
                interface="test.play_program/v1",
                operation="play",
                arguments={
                    "program": compute_result(
                        consumer_output_id,
                        value_type=Scalar(Payload("pulse_program")),
                    )
                },
            ),
        ),
    )
    environment = build_config_environment(
        config_with_physical_resources({"source-0": ("test.play_program/v1",)})
    )

    plan = materialize_local_execution(
        bind_program_facts(
            program,
            environment,
            experiment_id="bound-identity",
        )
    )
    repeated = materialize_local_execution(
        bind_program_facts(
            program,
            environment,
            experiment_id="bound-identity",
        )
    )

    assert [point.logical_id.logical_ordinal for point in plan.points] == [0, 1, 2]
    assert {
        (point.logical_id.domain_id.program_id, point.logical_id.domain_id.domain_id)
        for point in plan.points
    } == {("bound-identity", "root")}
    assert len({point.logical_id.value for point in plan.points}) == 3
    assert [point.logical_id.value for point in plan.points] == [
        point.logical_id.value for point in repeated.points
    ]

    payload_ids: list[str] = []
    [unused] = [
        call
        for call in operations_of_type(plan, ComputeOperation, point_index=0)
        if call.logical_compute_node_id == unused_id.qualified_name
    ]
    assert unused.payload_slot is None
    for point in plan.points:
        node_ids = [
            call.logical_compute_node_id
            for call in operations_of_type(
                plan, ComputeOperation, point_index=point.ordinal
            )
        ]
        assert node_ids.index(producer_id.qualified_name) < node_ids.index(
            consumer_id.qualified_name
        )
        consumer = next(
            call
            for call in operations_of_type(
                plan, ComputeOperation, point_index=point.ordinal
            )
            if call.logical_compute_node_id == consumer_id.qualified_name
        )
        assert consumer.inputs["value"] == OutputInput(
            producer_output_id,
            Scalar(Float()),
        )
        assert consumer.payload_slot is not None
        payload_ids.append(consumer.payload_slot.id)

        invocation = operations_of_type(
            plan,
            InvokeOperation,
            point_index=point.ordinal,
        )[0]
        payload_ref = invocation.arguments[0].value.root
        assert isinstance(payload_ref, PayloadRef)
        assert payload_ref.payload_id == consumer.payload_slot.id

    assert len(set(payload_ids)) == 3
    repeated_payload_ids: list[str] = []
    for point in repeated.points:
        repeated_consumer = next(
            call
            for call in operations_of_type(
                repeated,
                ComputeOperation,
                point_index=point.ordinal,
            )
            if call.logical_compute_node_id == consumer_id.qualified_name
        )
        assert repeated_consumer.payload_slot is not None
        repeated_payload_ids.append(repeated_consumer.payload_slot.id)
    assert payload_ids == repeated_payload_ids


def test_invocation_literal_payload_is_encoded_into_the_local_operation() -> None:
    schema_id = "tests.program/v1"
    program = program_fixture(
        point_domain=_point_domain(((),), Table(columns=())),
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=_resource("source-0"),
                capabilities=(InterfaceRef("test.play_program/v1"),),
            ),
        ),
        invocations=(
            instrument_invocation(
                id="play-program",
                resource_port_id=_resource("source-0"),
                interface="test.play_program/v1",
                operation="play",
                arguments={
                    "program": verified_scalar_expr(
                        lit(
                            PayloadValue(
                                schema_id=schema_id,
                                payload={"samples": [1.0, -1.0]},
                            )
                        ),
                        expected_type=Scalar(Payload(schema_id)),
                    )
                },
            ),
        ),
    )
    environment = build_config_environment(
        config_with_physical_resources({"source-0": ("test.play_program/v1",)})
    )
    codecs = json_payload_codecs(schema_id)

    with pytest.raises(CheckFailed) as missing_codec:
        materialize_local_execution(bind_program_facts(program, environment))

    [problem] = missing_codec.value.problems
    assert problem.code == "instrument_invocation_payload_encoding_failed"
    assert "no payload codec registered" in problem.message

    plan = materialize_local_execution(
        bind_program_facts(program, environment),
        payload_codecs=codecs,
    )

    [invocation] = operations_of_type(plan, InvokeOperation, point_index=0)
    [payload] = invocation.payloads
    [argument] = invocation.arguments
    assert argument.value.root == PayloadRef(payload_id=payload.id)
    assert payload.schema_id == schema_id
    assert codecs.decode(payload) == {"samples": [1.0, -1.0]}


def test_compute_inputs_are_normalized_before_binding() -> None:
    node_id = _operation_id("normalize-frequency")
    point_type = Table(
        columns=(
            TableColumn(
                "frequency",
                Scalar(QuantityType(dimension="frequency")),
            ),
        ),
    )
    program = program_fixture(
        point_domain=_point_domain(
            (
                (Quantity(value=5000.0, unit="MHz"),),
                (Quantity(value=5.0, unit="GHz"),),
            ),
            point_type,
        ),
        compute_nodes=(
            ComputeNodeFixture(
                id=node_id,
                implementation=_implementation(node_id, _quantity_value),
                inputs={
                    "frequency": verified_scalar_expr(
                        point_col(
                            "frequency",
                            Scalar(QuantityType(unit="GHz")),
                        ),
                        expected_type=Scalar(QuantityType(unit="GHz")),
                        bindings=_point_bindings(point_type),
                    )
                },
                result=_output(node_id, Scalar(Float())),
                input_types={
                    "frequency": Scalar(QuantityType(unit="GHz")),
                },
            ),
        ),
    )

    plan = materialize_local_execution(
        bind_program_facts(program, build_config_environment(load_config()))
    )

    calls = [
        operations_of_type(plan, ComputeOperation, point_index=point.ordinal)[0]
        for point in plan.points
    ]
    assert [call.inputs["frequency"] for call in calls] == [
        BoundInput(Quantity(value=5.0, unit="GHz")),
        BoundInput(Quantity(value=5.0, unit="GHz")),
    ]


def test_composed_module_input_keeps_its_declared_compute_input_type() -> None:
    play_interface = InterfaceRef("test.compute_input_type/v1")
    play = play_interface.operation("play")
    program_argument = play.argument("program")
    frequency = sc.coordinate(
        "frequency",
        sc.QuantityType(dimension="frequency"),
    )

    @sc.module(id="test.compute-input-type.child")
    def child(
        context: sc.ModuleContext,
        frequency_input: Annotated[
            sc.Input[Quantity],
            sc.QuantityType(unit="GHz"),
        ],
    ) -> None:
        program = context.compute(
            "build-program",
            fn=_wrap_value,
            inputs={"value": frequency_input},
            output_type=sc.ScalarType(sc.PayloadType("test.compute_input_type")),
        )
        drive = context._resource("drive", requires=(play_interface,))
        context._invoke(
            "play-program",
            resource=drive,
            operation=play,
            arguments={program_argument: program},
        )

    @sc.experiment(id="test.compute-input-type", kind="compute-input-type")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(child(frequency_input=frequency))
        experiment.grid(sc.axis(frequency, (5_000.0,), unit="MHz"))

    config = config_with_physical_resources({"drive-a": (play_interface.interface_id,)})
    plan = materialize_local_execution(
        bind_invocation(experiment.build(), config_profile=config)
    )

    [call] = operations_of_type(plan, ComputeOperation, point_index=0)
    assert call.inputs["value"] == BoundInput(Quantity(value=5.0, unit="GHz"))


def test_composed_state_expression_keeps_its_declared_value_type() -> None:
    set_frequency = InterfaceRef("test.set_frequency/v1")
    frequency = sc.coordinate(
        "frequency",
        sc.QuantityType(unit="MHz"),
    )

    @sc.module(id="test.state-value-type.child")
    def child(
        context: sc.ModuleContext,
        frequency_input: Annotated[
            sc.Input[Quantity],
            sc.QuantityType(unit="GHz"),
        ],
    ) -> None:
        drive = context._resource("drive", requires=(set_frequency,))
        context._bind_property(
            drive,
            set_frequency.property("value"),
            value=sc.input_ref(frequency_input) + Quantity(0.0, "GHz"),
        )

    @sc.experiment(id="test.state-value-type", kind="state-value-type")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(child(frequency_input=frequency))
        experiment.grid(sc.axis(frequency, (5_000.0,), unit="MHz"))

    config = load_config()
    plan = materialize_local_execution(
        bind_invocation(experiment.build(), config_profile=config)
    )

    [operation] = operations_of_type(plan, ApplyStateOperation, point_index=0)
    [target] = operation.targets
    assert target.value.root == Quantity(value=5.0, unit="GHz")


def test_compute_payload_input_rejects_mismatched_schema_before_binding() -> None:
    point_type = Table(
        columns=(TableColumn("payload", Scalar(Payload("source-payload"))),),
    )

    with pytest.raises(ExpressionVerificationError) as caught:
        verified_scalar_expr(
            point_col("payload", Scalar(Payload("source-payload"))),
            expected_type=Scalar(Payload("expected-payload")),
            bindings=_point_bindings(point_type),
        )

    assert caught.value.code == "incompatible_result_type"


def test_compute_mapping_inputs_preserve_key_types_and_values() -> None:
    node_id = _operation_id("consume-mapping")
    point_type = Table(
        columns=(TableColumn("payload", Scalar(Payload("mapping"))),),
    )
    program = program_fixture(
        point_domain=_point_domain(
            (
                (
                    PayloadValue(
                        schema_id="mapping",
                        payload={1: "a", "1": "b"},
                    ),
                ),
                (
                    PayloadValue(
                        schema_id="mapping",
                        payload={1: "z", "1": "b"},
                    ),
                ),
            ),
            point_type,
        ),
        compute_nodes=(
            ComputeNodeFixture(
                id=node_id,
                implementation=_implementation(node_id, _mapping_size),
                inputs={
                    "payload": verified_scalar_expr(
                        point_col("payload", Scalar(Payload("mapping"))),
                        expected_type=Scalar(Payload("mapping")),
                        bindings=_point_bindings(point_type),
                    )
                },
                result=_output(node_id, Scalar(Float())),
                input_types={"payload": Scalar(Payload("mapping"))},
            ),
        ),
    )

    plan = materialize_local_execution(
        bind_program_facts(program, build_config_environment(load_config()))
    )

    assert (
        operations_of_type(plan, ComputeOperation, point_index=0)[0].inputs
        != operations_of_type(plan, ComputeOperation, point_index=1)[0].inputs
    )


def test_opaque_point_value_does_not_participate_in_logical_identity() -> None:
    program = program_fixture(
        point_domain=_point_domain(
            (
                (
                    PayloadValue(
                        schema_id="opaque",
                        payload=object(),
                    ),
                ),
            ),
            Table(
                columns=(TableColumn("payload", Scalar(Payload("opaque"))),),
            ),
        ),
    )

    plan = materialize_local_execution(
        bind_program_facts(program, build_config_environment(load_config()))
    )

    assert len(plan.points) == 1
