from __future__ import annotations

from typing import Annotated, Literal

import pytest
from scopecat_testkit.authoring import bind_invocation, load_config
from scopecat_testkit.domain import domain_call
from scopecat_testkit.materialized_effects import materialized_effects_contract

import scopecat as sc
from scopecat.compiler.value_resolution import resolve_bound_value
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.problems import model_location
from scopecat.program.domain import domain_program
from scopecat.program.expressions import PointColumnScalarExpr
from scopecat.program.values import ParameterKeyInput
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.parameter import (
    ParameterDefinition,
    TableParameterValue,
)


def _identity(value: object) -> object:
    return value


def _resolve_dependency(
    value: sc.ValueRef,
    config: ConfigProfileSnapshot,
) -> None:
    _resolve_root_domain_dependency(value, config)


def _resolve_table_dependency(
    value: sc.ValueRef,
    config: ConfigProfileSnapshot,
) -> None:
    _resolve_root_domain_dependency(value, config)


def _resolve_root_domain_dependency(
    value: sc.ValueRef,
    config: ConfigProfileSnapshot,
) -> None:
    program = domain_program(
        "consume-parameter",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        compiler_inputs={"value": value.value_type},
    )

    @sc.experiment(id="test.parameter-contract", kind="parameter_contract")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(
            domain_call(
                program,
                compiler_inputs={"value": value},
            )
        )

    bind_invocation(experiment.build(), config_profile=config)


def _empty_module(id: str) -> sc.ExperimentModule[None, ...]:
    @sc.module(id=id)
    def module(context: sc.ModuleContext) -> None:
        del context

    return module


def _axis_invocation(id: str, *axes: sc.Axis) -> sc.ExperimentInvocation:
    module = _empty_module(id)

    @sc.experiment(id=id, kind="parameter_contract")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(module())
        experiment.grid(*axes)

    return experiment.build()


def _config_with_parameter_table(
    *,
    frequency_type: sc.ScalarType | None = None,
) -> ConfigProfileSnapshot:
    config = load_config()
    definition = ParameterDefinition(
        id="device_parameters",
        value_type=sc.TableType(
            primary_key=("device",),
            columns=(
                sc.TableColumn(
                    id="device",
                    value_type=sc.ScalarType(
                        sc.EntityType(entity_kind="logical_device")
                    ),
                ),
                sc.TableColumn(
                    id="frequency",
                    value_type=frequency_type
                    or sc.ScalarType(sc.QuantityType(unit="GHz")),
                ),
            ),
        ),
    )
    system = config.system.model_copy(
        update={
            "parameter_catalog": config.parameter_catalog.model_copy(
                update={
                    "definitions": (*config.parameter_catalog.definitions, definition)
                }
            )
        }
    )
    parameter_snapshot = config.parameter_snapshot.model_copy(
        update={
            "values": [
                *config.parameter_snapshot.values,
                TableParameterValue(
                    id="device_parameters",
                    rows=[
                        {
                            "device": "q0",
                            "frequency": sc.Quantity(value=5.0, unit="GHz"),
                        }
                    ],
                ),
            ]
        }
    )
    return config.model_copy(
        update={"system": system, "parameter_snapshot": parameter_snapshot}
    )


def _config_with_literal_key_type(
    key_type: sc.ScalarType,
) -> ConfigProfileSnapshot:
    config = load_config()
    definition = ParameterDefinition(
        id="literal_key_parameters",
        value_type=sc.TableType(
            primary_key=("key",),
            columns=(
                sc.TableColumn(id="key", value_type=key_type),
                sc.TableColumn(
                    id="value",
                    value_type=sc.ScalarType(sc.StringType()),
                ),
            ),
        ),
    )
    system = config.system.model_copy(
        update={
            "parameter_catalog": config.parameter_catalog.model_copy(
                update={
                    "definitions": (*config.parameter_catalog.definitions, definition)
                }
            )
        }
    )
    parameter_snapshot = config.parameter_snapshot.model_copy(
        update={
            "values": (
                *config.parameter_snapshot.values,
                TableParameterValue(id="literal_key_parameters"),
            )
        }
    )
    return config.model_copy(
        update={"system": system, "parameter_snapshot": parameter_snapshot}
    )


def test_scalar_parameter_declaration_is_checked_against_catalog() -> None:
    _resolve_dependency(
        sc.parameter(
            "drive_frequency",
            sc.ScalarType(sc.QuantityType(unit="MHz")),
        ),
        load_config(),
    )

    with pytest.raises(CheckFailed) as error:
        _resolve_dependency(
            sc.parameter(
                "drive_frequency",
                sc.ScalarType(sc.StringType()),
            ),
            load_config(),
        )

    assert error.value.problems[0].code == "authoring_parameter_type_mismatch"


def test_unknown_scalar_parameter_has_authoring_problem() -> None:
    with pytest.raises(CheckFailed) as error:
        _resolve_dependency(
            sc.parameter(
                "missing_frequency",
                sc.ScalarType(sc.QuantityType()),
            ),
            load_config(),
        )

    assert error.value.problems[0].code == "unknown_authoring_parameter"
    assert error.value.problems[0].location == model_location(
        "parameters", "missing_frequency"
    )


def test_parameter_contract_survives_nested_elaboration() -> None:
    @sc.module(id="test.parameter-contract-child")
    def child(
        context: sc.ModuleContext,
        frequency: Annotated[sc.Input[str], sc.StringType()],
    ) -> None:
        context.compute(
            "consume-child-frequency",
            fn=_identity,
            inputs={"value": frequency},
            output_type=sc.ScalarType(sc.StringType()),
        )

    @sc.module(id="test.parameter-contract-parent")
    def parent(
        context: sc.ModuleContext,
        frequency: Annotated[sc.Input[str], sc.StringType()],
    ) -> None:
        context.use(
            child.instantiate(
                "parameter-contract-child",
                frequency=frequency,
            )
        )

    parameter = sc.parameter(
        "drive_frequency",
        sc.StringType(),
    )

    @sc.experiment(id="test.parameter-contract", kind="parameter_contract")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(parent(frequency=parameter))

    with pytest.raises(CheckFailed) as error:
        bind_invocation(experiment.build(), config_profile=load_config())

    assert error.value.problems[0].code == "authoring_parameter_type_mismatch"


def test_parameter_contract_survives_scan_lowering() -> None:
    invocation = _axis_invocation(
        "test.parameter-contract-scan",
        sc.axis(
            sc.coordinate(
                "frequency",
                sc.ScalarType(sc.QuantityType()),
            ),
            center=sc.parameter(
                "drive_frequency",
                sc.ScalarType(sc.QuantityType(unit="ns")),
            ),
            span=sc.Quantity(value=100, unit="ns"),
            points=3,
        ),
    )

    with pytest.raises(CheckFailed) as error:
        bind_invocation(
            invocation,
            config_profile=load_config(),
        )

    assert error.value.problems[0].code == "authoring_parameter_type_mismatch"


@pytest.mark.parametrize(
    ("column", "point_type", "values", "expected_code"),
    [
        (
            "missing",
            sc.ScalarType(sc.StringType()),
            ["value"],
            "unknown_authoring_parameter_column",
        ),
        (
            "frequency",
            sc.ScalarType(sc.StringType()),
            ["value"],
            "authoring_parameter_column_type_mismatch",
        ),
    ],
)
def test_parameter_overlay_target_is_checked_against_catalog_column(
    column: str,
    point_type: sc.ScalarType,
    values: list[str],
    expected_code: str,
) -> None:
    lookup = sc.parameter_lookup(
        "device_parameters",
        key={"device": "q0"},
        column=column,
        value_type=point_type,
    )
    scan = sc.axis(
        sc.coordinate("scanned_value", point_type),
        values,
        overlay=lookup,
    )
    invocation = _axis_invocation("test.parameter-contract-overlay-target", scan)

    with pytest.raises(CheckFailed) as error:
        bind_invocation(
            invocation,
            config_profile=_config_with_parameter_table(),
        )

    assert error.value.problems[0].code == expected_code


def test_parameter_overlay_retains_row_key_parameter_contracts() -> None:
    lookup = sc.parameter_lookup(
        "device_parameters",
        key={
            "device": sc.parameter(
                "drive_frequency",
                sc.ScalarType(sc.StringType()),
            )
        },
        column="frequency",
        value_type=sc.ScalarType(sc.QuantityType(unit="GHz")),
    )
    scan = sc.axis(
        sc.coordinate(
            "scanned_frequency",
            sc.ScalarType(sc.QuantityType(unit="GHz")),
        ),
        [5.0],
        overlay=lookup,
        unit="GHz",
    )
    invocation = _axis_invocation("test.parameter-contract-overlay-key", scan)

    with pytest.raises(CheckFailed) as error:
        bind_invocation(
            invocation,
            config_profile=_config_with_parameter_table(),
        )

    assert (
        error.value.problems[0].code == "authoring_parameter_lookup_key_type_mismatch"
    )


def test_around_axis_overlay_materializes_about_the_current_table_cell() -> None:
    config = _config_with_parameter_table()
    frequency_type = sc.ScalarType(sc.QuantityType(unit="GHz"))
    frequency = sc.coordinate("scanned_frequency", frequency_type)
    invocation = _axis_invocation(
        "test.parameter-around-overlay",
        sc.axis(
            frequency,
            overlay=sc.parameter_lookup(
                "device_parameters",
                key={"device": "q0"},
                column="frequency",
                value_type=frequency_type,
            ),
            span="200 MHz",
            points=3,
        ),
    )

    resolved = bind_invocation(invocation, config_profile=config)
    materialized = materialized_effects_contract(
        resolved,
        resolved.environment.parameters,
        config=config,
    )

    scanned = [point.coordinates["scanned_frequency"] for point in materialized.points]
    assert scanned == [
        sc.Quantity(4.9, "GHz"),
        sc.Quantity(5.0, "GHz"),
        sc.Quantity(5.1, "GHz"),
    ]
    assert len(resolved.bindings.parameter_overlays) == 1
    stored = config.parameter_snapshot.get("device_parameters")
    assert isinstance(stored, TableParameterValue)
    assert stored.rows[0]["frequency"] == sc.Quantity(5.0, "GHz")


def test_range_axis_overlay_materializes_literal_endpoints() -> None:
    config = _config_with_parameter_table()
    frequency_type = sc.ScalarType(sc.QuantityType(unit="GHz"))
    frequency = sc.coordinate("scanned_frequency", frequency_type)
    invocation = _axis_invocation(
        "test.parameter-range-overlay",
        sc.axis(
            frequency,
            overlay=sc.parameter_lookup(
                "device_parameters",
                key={"device": "q0"},
                column="frequency",
                value_type=frequency_type,
            ),
            start=4.8,
            stop=5.2,
            unit="GHz",
            points=3,
        ),
    )

    resolved = bind_invocation(invocation, config_profile=config)
    materialized = materialized_effects_contract(
        resolved,
        resolved.environment.parameters,
        config=config,
    )

    assert [
        point.coordinates["scanned_frequency"] for point in materialized.points
    ] == [
        sc.Quantity(4.8, "GHz"),
        sc.Quantity(5.0, "GHz"),
        sc.Quantity(5.2, "GHz"),
    ]
    assert len(resolved.bindings.parameter_overlays) == 1


def test_parameter_overlay_specializes_consumers_against_its_point_column() -> None:
    frequency_type = sc.ScalarType(sc.QuantityType(unit="GHz"))
    frequency = sc.coordinate("scanned_frequency", frequency_type)
    lookup = sc.parameter_lookup(
        "device_parameters",
        key={"device": "q0"},
        column="frequency",
        value_type=frequency_type,
    )
    program = domain_program(
        "consume-scanned-parameter",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        inputs={"frequency": frequency_type},
    )

    @sc.experiment(id="test.parameter-overlay-consumer", kind="parameter_contract")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(domain_call(program, inputs={"frequency": lookup}))
        experiment.grid(
            sc.axis(
                frequency,
                overlay=lookup,
                span="200 MHz",
                points=3,
            )
        )

    resolved = bind_invocation(
        experiment.build(),
        config_profile=_config_with_parameter_table(),
    )

    [execution] = resolved.program.program.domain_executions
    expression = resolve_bound_value(
        resolved.program,
        resolved.bindings,
        dict(execution.inputs)["frequency"],
    )
    assert isinstance(expression, PointColumnScalarExpr)
    assert expression.name == "scanned_frequency"


def test_parameter_overlay_type_must_be_writable_to_catalog_column() -> None:
    bounded_frequency = sc.ScalarType(
        sc.QuantityType(unit="GHz", minimum=4.0, maximum=6.0)
    )
    config = _config_with_parameter_table(frequency_type=bounded_frequency)
    frequency_type = sc.ScalarType(sc.QuantityType(unit="GHz"))
    lookup = sc.parameter_lookup(
        "device_parameters",
        key={"device": "q0"},
        column="frequency",
        value_type=frequency_type,
    )
    scan = sc.axis(
        sc.coordinate("scanned_frequency", frequency_type),
        [5.0],
        overlay=lookup,
        unit="GHz",
    )
    invocation = _axis_invocation("test.parameter-overlay-write-type", scan)

    with pytest.raises(CheckFailed) as error:
        bind_invocation(invocation, config_profile=config)

    assert error.value.problems[0].code == "authoring_parameter_overlay_type_mismatch"


def test_parameter_lookup_checks_table_column_and_entity_type() -> None:
    config = _config_with_parameter_table()
    _resolve_dependency(
        sc.parameter_lookup(
            "device_parameters",
            key={"device": "q0"},
            column="device",
            value_type=sc.ScalarType(sc.EntityType()),
        ),
        config,
    )

    with pytest.raises(CheckFailed) as missing_column:
        _resolve_dependency(
            sc.parameter_lookup(
                "device_parameters",
                key={"device": "q0"},
                column="missing",
                value_type=sc.ScalarType(sc.StringType()),
            ),
            config,
        )
    with pytest.raises(CheckFailed) as wrong_entity_kind:
        _resolve_dependency(
            sc.parameter_lookup(
                "device_parameters",
                key={"device": "q0"},
                column="device",
                value_type=sc.ScalarType(sc.EntityType(entity_kind="coupler")),
            ),
            config,
        )

    assert missing_column.value.problems[0].code == (
        "unknown_authoring_parameter_column"
    )
    assert wrong_entity_kind.value.problems[0].code == (
        "authoring_parameter_column_type_mismatch"
    )


def test_parameter_lookup_checks_primary_key_shape_and_typed_key_values() -> None:
    config = _config_with_parameter_table()

    @sc.module(id="test.typed-parameter-key")
    def module(
        context: sc.ModuleContext,
        frequency: Annotated[sc.Input[sc.Quantity], sc.QuantityType(unit="GHz")],
    ) -> None:
        context.compute(
            "consume-typed-parameter-key",
            fn=_identity,
            inputs={"value": frequency},
            output_type=sc.ScalarType(sc.QuantityType(unit="GHz")),
        )

    @sc.experiment(id="test.typed-parameter-key", kind="parameter_contract")
    def experiment(
        experiment: sc.ExperimentContext,
        device: Annotated[
            sc.Input[sc.EntityRef | str],
            sc.EntityType(entity_kind="logical_device"),
        ],
    ) -> None:
        experiment.use(
            module(
                frequency=sc.parameter_lookup(
                    "device_parameters",
                    key={"device": device},
                    column="frequency",
                    value_type=sc.QuantityType(unit="GHz"),
                )
            )
        )

    bind_invocation(
        experiment.build(device="q0"),
        config_profile=config,
    )

    with pytest.raises(CheckFailed) as wrong_key_shape:
        _resolve_dependency(
            sc.parameter_lookup(
                "device_parameters",
                key={"other": "q0"},
                column="frequency",
                value_type=sc.ScalarType(sc.QuantityType(unit="GHz")),
            ),
            config,
        )
    with pytest.raises(CheckFailed) as wrong_key_type:

        @sc.module(id="test.wrong-parameter-key")
        def wrong_module(
            context: sc.ModuleContext,
            frequency: Annotated[
                sc.Input[sc.Quantity],
                sc.QuantityType(unit="GHz"),
            ],
        ) -> None:
            context.compute(
                "consume-wrong-parameter-key",
                fn=_identity,
                inputs={"value": frequency},
                output_type=sc.ScalarType(sc.QuantityType(unit="GHz")),
            )

        @sc.experiment(id="test.wrong-parameter-key", kind="parameter_contract")
        def wrong_experiment(
            experiment: sc.ExperimentContext,
            device: Annotated[
                sc.Input[sc.EntityRef | str],
                sc.EntityType(entity_kind="logical_coupler"),
            ],
        ) -> None:
            experiment.use(
                wrong_module(
                    frequency=sc.parameter_lookup(
                        "device_parameters",
                        key={"device": device},
                        column="frequency",
                        value_type=sc.QuantityType(unit="GHz"),
                    )
                )
            )

        bind_invocation(wrong_experiment.build(device="q0"), config_profile=config)

    assert wrong_key_shape.value.problems[0].code == (
        "authoring_parameter_lookup_key_mismatch"
    )
    assert wrong_key_type.value.problems[0].code == (
        "authoring_parameter_lookup_key_type_mismatch"
    )


@pytest.mark.parametrize(
    ("key_type", "literal"),
    [
        (sc.ScalarType(sc.BoolType()), 1),
        (sc.ScalarType(sc.IntType()), True),
        (sc.ScalarType(sc.FloatType()), "1.0"),
        (sc.ScalarType(sc.StringType()), 1),
        (
            sc.ScalarType(sc.QuantityType(unit="GHz")),
            1.0,
        ),
        (
            sc.ScalarType(sc.StringType()),
            sc.Quantity(value=1.0, unit="GHz"),
        ),
    ],
)
def test_parameter_lookup_checks_every_literal_key_type(
    key_type: sc.ScalarType,
    literal: ParameterKeyInput,
) -> None:
    with pytest.raises(CheckFailed) as error:
        _resolve_dependency(
            sc.parameter_lookup(
                "literal_key_parameters",
                key={"key": literal},
                column="value",
                value_type=sc.ScalarType(sc.StringType()),
            ),
            _config_with_literal_key_type(key_type),
        )

    assert error.value.problems[0].code == (
        "authoring_parameter_lookup_key_type_mismatch"
    )


def test_parameter_table_declaration_is_checked_against_catalog_schema() -> None:
    config = _config_with_parameter_table()
    valid_table = sc.TableType(
        columns=(
            sc.TableColumn(
                "device",
                sc.ScalarType(sc.EntityType()),
            ),
            sc.TableColumn(
                "frequency",
                sc.ScalarType(sc.QuantityType()),
            ),
        ),
    )
    _resolve_table_dependency(
        sc.parameter("device_parameters", valid_table),
        config,
    )

    incompatible_table = sc.TableType(
        columns=(
            sc.TableColumn(
                "frequency",
                sc.ScalarType(sc.StringType()),
            ),
        ),
    )
    with pytest.raises(CheckFailed) as error:
        _resolve_table_dependency(
            sc.parameter("device_parameters", incompatible_table),
            config,
        )

    assert error.value.problems[0].code == ("authoring_parameter_type_mismatch")


def test_unknown_parameter_table_has_authoring_problem() -> None:
    value_type = sc.TableType(
        columns=(
            sc.TableColumn(
                "device",
                sc.ScalarType(sc.EntityType()),
            ),
        )
    )

    with pytest.raises(CheckFailed) as error:
        _resolve_table_dependency(
            sc.parameter("missing_table", value_type),
            load_config(),
        )

    assert error.value.problems[0].code == "unknown_authoring_parameter"
    assert error.value.problems[0].location == model_location(
        "parameters", "missing_table"
    )


class GateCalibration(sc.ParameterModel, table="gate_calibration"):
    operation: sc.Param[Literal["x", "x90"]] = sc.param(key=True)
    amplitude: sc.Magnitude[float] = sc.quantity(unit="arb")


def test_literal_parameter_key_preserves_its_choice_constraint() -> None:
    config = load_config()
    config.system = config.system.model_copy(
        update={"parameter_catalog": sc.parameter_catalog("gates", GateCalibration)}
    )
    config.parameter_snapshot = sc.parameter_snapshot(
        "gates",
        tables={GateCalibration: (GateCalibration(operation="x90", amplitude=0.1),)},
    )
    _resolve_dependency(sc.parameter_ref(GateCalibration.amplitude, "x90"), config)
    with pytest.raises(CheckFailed) as rejected:
        _resolve_dependency(sc.parameter_ref(GateCalibration.amplitude, "z"), config)
    assert (
        rejected.value.problems[0].code
        == "authoring_parameter_lookup_key_type_mismatch"
    )
