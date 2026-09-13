# pyright: reportUnusedFunction=false

from __future__ import annotations

from typing import Annotated

import pytest
from scopecat_testkit.authoring import (
    DRIVE_FREQUENCY_POINT,
    bind_invocation,
    load_config,
    simple_experiment,
)
from scopecat_testkit.expressions import evaluate_scalar
from scopecat_testkit.materialized_effects import (
    config_with_physical_resources,
    materialized_effects_contract,
    materialized_state_properties,
    measurement_projection_contract,
)
from scopecat_testkit.routing import routing_endpoint, routing_graph

import scopecat as sc
import scopecat.authoring as authoring
from scopecat.compiler.frontend.elaboration import compose_module
from scopecat.compiler.frontend.resolution import (
    compile_invocation,
)
from scopecat.compiler.relations.context import EvalContext
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.quantity import Quantity
from scopecat.program.expression_analysis import expression_input_refs
from scopecat.program.expressions import (
    InputScalarExpr,
    LiteralScalarExpr,
    ScalarExpr,
    param,
)
from scopecat.program.logical import (
    LogicalProgram,
)
from scopecat.program.value_refs import (
    ValueRef,
    internal_lower_scalar_value_ref,
    internal_value_ref_parameter_contracts,
    internal_value_ref_point_dependencies,
)
from scopecat.program.values import compute as program_compute
from scopecat.records.parameter import (
    ParameterDefinition,
    TableParameterValue,
)
from scopecat.sdk.instruments import InterfaceRef


def _identity_value(*, value: object) -> object:
    return value


def _collect_values(**values: object) -> dict[str, object]:
    return values


def _logical_binding_expression(
    program: LogicalProgram,
    index: int,
) -> ScalarExpr:
    value_id = program.bindings[index].value_id
    definition = next(item for item in program.value_defs if item.id == value_id)
    source = definition.source
    assert isinstance(source, ScalarExpr)
    return source


def _table_definition(
    *,
    id: str,
    primary_key: list[str],
    columns: list[sc.TableColumn],
) -> ParameterDefinition:
    return ParameterDefinition(
        id=id,
        value_type=sc.TableType(
            columns=tuple(columns),
            primary_key=tuple(primary_key),
        ),
    )


_QUANTITY_VALUE = authoring.ScalarType(authoring.QuantityType())

_SET_FREQUENCY = InterfaceRef("test.set_frequency/v1")
_SET_FREQUENCY_VALUE = _SET_FREQUENCY.property("frequency")
_SET_FREQUENCY_SIGNAL = _SET_FREQUENCY.acquisition("sample").result("signal")
_SCALAR_SIGNAL = InterfaceRef("test.scalar_signal/v1")
_SCALAR_SIGNAL_VALUE = _SCALAR_SIGNAL.acquisition("sample").result("signal")
_PLAY_PULSE_PROGRAM = InterfaceRef("test.play_pulse_program/v1")
_PLAY_PULSE = _PLAY_PULSE_PROGRAM.operation("play")
_PLAY_PULSE_PROGRAM_ARGUMENT = _PLAY_PULSE.argument("program")
_SET_OFFSET = InterfaceRef("test.set_offset/v1")
_SET_OFFSET_VALUE = _SET_OFFSET.property("offset")
_SET_GAIN = InterfaceRef("test.set_gain/v1")
_SET_GAIN_VALUE = _SET_GAIN.property("gain")
_DRIVE_FREQUENCY = InterfaceRef("test.drive_frequency/v1")
_DRIVE_FREQUENCY_VALUE = _DRIVE_FREQUENCY.property("value")

type _EntityInput = Annotated[
    sc.Input[EntityRef | str],
    sc.EntityType(),
]
type _LogicalDeviceInput = Annotated[
    sc.Input[EntityRef | str],
    sc.EntityType(entity_kind="logical_device"),
]
type _QuantityInput = Annotated[
    sc.Input[Quantity],
    sc.QuantityType(),
]
type _FloatInput = Annotated[sc.Input[float], sc.FloatType()]


def test_module_invoke_rejects_argument_from_another_operation() -> None:
    unrelated = _PLAY_PULSE_PROGRAM.operation("preview").argument("program")

    with pytest.raises(
        ValueError,
        match="arguments must belong to the selected operation",
    ):

        @sc.module(id="test.invoke.argument-target")
        def module(context: sc.ModuleContext) -> None:
            drive = context._resource("drive", requires=(_PLAY_PULSE_PROGRAM,))
            context._invoke(
                "play-program",
                resource=drive,
                operation=_PLAY_PULSE,
                arguments={unrelated: True},
            )


def test_module_invocation_resolves_roles_scans_and_bindings() -> None:
    experiment_definition = simple_experiment()
    assert experiment_definition.bind().definition.metadata == {
        "assembled_by": "experiment"
    }

    resolved = bind_invocation(
        experiment_definition.bind(subject="q0"),
        config_profile=load_config(),
    )

    experiment = resolved
    assert resolved.program.experiment_id == "test.simple_scan"
    assert resolved.program.kind == "simple_scan"
    preview = materialized_effects_contract(
        experiment, resolved.environment.parameters, config=load_config()
    )
    projection = measurement_projection_contract(
        experiment, resolved.environment.parameters, config=load_config()
    )

    assert projection.coordinate_ids == ("drive_frequency",)
    assert preview.points[0].coordinates["drive_frequency"] == Quantity(
        value=4.9, unit="GHz"
    )
    assert [record.id for record in experiment.bindings.record_uses] == ["signal"]
    _, state, target = materialized_state_properties(preview)[0]
    assert state.instrument_id == "source-0"
    assert target.interface_id == "test.set_frequency/v1"
    assert target.property_id == "frequency"
    assert target.value.root == Quantity(value=4.9, unit="GHz")


def test_compute_inputs_close_experiment_inputs_before_logical_verification() -> None:
    def build_program(
        *,
        qubit: object,
        length: object,
        frequency: object,
    ) -> dict[str, object]:
        return {
            "qubit": qubit,
            "length": length,
            "frequency": frequency,
        }

    @sc.module(id="test.compute_provenance")
    def module(
        context: sc.ModuleContext,
        qubit: _EntityInput,
        pulse_length: _QuantityInput,
        frequency: _QuantityInput,
    ) -> None:
        qubit_ref = sc.input_ref(qubit)
        build = context.compute(
            "build-program",
            fn=build_program,
            output_type=authoring.ScalarType(authoring.PayloadType("pulse")),
            inputs={
                "qubit": qubit_ref,
                "length": pulse_length,
                "frequency": frequency,
            },
        )
        drive = context._resource("drive", requires=(_PLAY_PULSE_PROGRAM,))
        context._invoke(
            "play-program",
            resource=drive,
            operation=_PLAY_PULSE,
            arguments={_PLAY_PULSE_PROGRAM_ARGUMENT: build},
        )

    @sc.experiment(id="test.compute_provenance", kind="compute_provenance")
    def experiment(
        experiment: sc.ExperimentContext,
        qubit: _EntityInput,
        pulse_length: _QuantityInput,
    ) -> None:
        qubit_ref = sc.input_ref(qubit)
        experiment.use(
            module(
                qubit=qubit,
                pulse_length=pulse_length,
                frequency=sc.parameter_lookup(
                    "sample_qubits",
                    key={"qubit": qubit_ref},
                    column="drive_frequency",
                    value_type=authoring.QuantityType(),
                ),
            )
        )

    compiled = compile_invocation(
        experiment.build(
            qubit="q0",
            pulse_length=Quantity(value=20.0, unit="ns"),
        )
    )
    logical_program = compiled.program.program
    operation = next(
        operation
        for operation in logical_program.compute_nodes
        if operation.id.local_id == "build-program"
    )
    definitions = {
        definition.id: definition for definition in logical_program.value_defs
    }
    uses = dict(operation.inputs)

    assert all(isinstance(use, ValueId) for use in uses.values())
    qubit_source = definitions[uses["qubit"]].source
    length_source = definitions[uses["length"]].source
    frequency_source = definitions[uses["frequency"]].source
    assert isinstance(qubit_source, ScalarExpr)
    assert isinstance(length_source, ScalarExpr)
    assert isinstance(frequency_source, ScalarExpr)
    assert all(
        expression_input_refs(compiled.program.scalar_values[value_id]) == ()
        for value_id in uses.values()
    )
    assert operation.result_type == authoring.ScalarType(authoring.PayloadType("pulse"))


def test_compute_function_signature_must_match_explicit_inputs() -> None:
    output_type = authoring.ScalarType(authoring.StringType())

    with pytest.raises(TypeError, match="does not match declared inputs"):
        program_compute(
            "missing-input",
            fn=_identity_value,
            output_type=output_type,
        )

    with pytest.raises(TypeError, match="must use explicit named parameters"):
        program_compute(
            "variadic-inputs",
            fn=_collect_values,
            inputs={"value": "declared"},
            output_type=output_type,
        )


def test_runtime_entity_scan_feeds_resource_selection_and_parameter_lookup() -> None:
    seed_config = load_config()
    source_0 = seed_config.instrument_registry.instruments[0]
    topology = seed_config.topology.model_copy(
        update={
            "entities": [
                *seed_config.topology.entities,
                EntityRef(id="q1", kind="logical_device"),
            ],
        }
    )
    catalog = seed_config.parameter_catalog.model_copy(
        update={
            "definitions": [
                *seed_config.parameter_catalog.definitions,
                _table_definition(
                    id="sample_qubits",
                    primary_key=["qubit"],
                    columns=[
                        sc.TableColumn(
                            id="qubit",
                            value_type=sc.ScalarType(
                                sc.EntityType(entity_kind="logical_device")
                            ),
                        ),
                        sc.TableColumn(
                            id="drive_frequency",
                            value_type=sc.ScalarType(sc.QuantityType(unit="GHz")),
                        ),
                    ],
                ),
            ]
        }
    )
    parameter_snapshot = seed_config.parameter_snapshot.model_copy(
        update={
            "values": [
                *seed_config.parameter_snapshot.values,
                TableParameterValue(
                    id="sample_qubits",
                    rows=[
                        {
                            "qubit": "q0",
                            "drive_frequency": Quantity(value=5.0, unit="GHz"),
                        },
                        {
                            "qubit": "q1",
                            "drive_frequency": Quantity(value=5.1, unit="GHz"),
                        },
                    ],
                ),
            ]
        }
    )
    system = seed_config.system.model_copy(
        update={
            "topology": topology,
            "parameter_catalog": catalog,
            "instrument_registry": seed_config.instrument_registry.model_copy(
                update={
                    "instruments": [
                        source_0,
                        source_0.model_copy(
                            update={
                                "id": "source-1",
                                "exclusivity_key": "source-1",
                            }
                        ),
                    ]
                }
            ),
            "routing": routing_graph(
                bindings=[
                    routing_endpoint(
                        instrument_id="source-0",
                        interface_id="test.set_frequency/v1",
                        entity_id="q0",
                        channel_id="drive-q0",
                    ),
                    routing_endpoint(
                        instrument_id="source-1",
                        interface_id="test.set_frequency/v1",
                        entity_id="q1",
                        channel_id="drive-q1",
                    ),
                ],
            ),
        }
    )
    config = seed_config.model_copy(
        update={"system": system, "parameter_snapshot": parameter_snapshot}
    )
    qubit = sc.coordinate(
        "qubit",
        authoring.EntityType(entity_kind="logical_device"),
    )

    @sc.module(id="test.runtime_entity_scan")
    def module(
        context: sc.ModuleContext,
        qubit_input: _LogicalDeviceInput,
        drive_frequency: _QuantityInput,
    ) -> sc.ProductRef:
        qubit_ref = sc.input_ref(qubit_input)
        drive = context._resource(
            "drive",
            requires=(_SET_FREQUENCY,),
            for_entities=(qubit_ref,),
        )
        context._bind_property(
            drive,
            _SET_FREQUENCY_VALUE,
            value=drive_frequency,
        )
        signal = context._product("signal", unit="ratio")
        context._acquire(
            "read-signal",
            resource=drive,
            results={_SET_FREQUENCY_SIGNAL: signal},
        )
        return signal

    @sc.experiment(id="test.runtime_entity_scan", kind="runtime_entity_scan")
    def experiment(experiment: sc.ExperimentContext) -> None:
        signal = experiment.use(
            module(
                qubit_input=qubit,
                drive_frequency=authoring.parameter_lookup(
                    "sample_qubits",
                    key={"qubit": qubit},
                    column="drive_frequency",
                    value_type=authoring.QuantityType(unit="GHz"),
                ),
            )
        )
        experiment.alias(signal)

    resolved = bind_invocation(
        experiment.bind().with_axis(
            sc.axis(
                qubit,
                ["q0", "q1"],
            )
        ),
        config_profile=config,
    )
    preview = materialized_effects_contract(
        resolved,
        resolved.environment.parameters,
        config=config,
    )

    assert [point.coordinates["qubit"] for point in preview.points] == [
        EntityRef(id="q0", kind="logical_device"),
        EntityRef(id="q1", kind="logical_device"),
    ]
    assert [
        (point_index, state.instrument_id, target.value.root)
        for point_index, state, target in materialized_state_properties(preview)
    ] == [
        (0, "source-0", Quantity(value=5.0, unit="GHz")),
        (1, "source-1", Quantity(value=5.1, unit="GHz")),
    ]


def test_bound_entity_input_can_select_a_default_parameter_lookup_center() -> None:
    seed_config = load_config()
    topology = seed_config.topology.model_copy(
        update={
            "entities": [
                *seed_config.topology.entities,
                EntityRef(id="q1", kind="logical_device"),
            ],
        }
    )
    catalog = seed_config.parameter_catalog.model_copy(
        update={
            "definitions": [
                *seed_config.parameter_catalog.definitions,
                _table_definition(
                    id="sample_qubits",
                    primary_key=["qubit"],
                    columns=[
                        sc.TableColumn(
                            id="qubit",
                            value_type=sc.ScalarType(
                                sc.EntityType(entity_kind="logical_device")
                            ),
                        ),
                        sc.TableColumn(
                            id="rabi_length",
                            value_type=sc.ScalarType(sc.QuantityType(unit="ns")),
                        ),
                    ],
                ),
            ]
        }
    )
    parameter_snapshot = seed_config.parameter_snapshot.model_copy(
        update={
            "values": [
                *seed_config.parameter_snapshot.values,
                TableParameterValue(
                    id="sample_qubits",
                    rows=[
                        {
                            "qubit": "q0",
                            "rabi_length": Quantity(value=40.0, unit="ns"),
                        },
                        {
                            "qubit": "q1",
                            "rabi_length": Quantity(value=80.0, unit="ns"),
                        },
                    ],
                ),
            ]
        }
    )
    system = seed_config.system.model_copy(
        update={"topology": topology, "parameter_catalog": catalog},
    )
    config = seed_config.model_copy(
        update={"system": system, "parameter_snapshot": parameter_snapshot}
    )

    @sc.module(id="test.runtime_entity_dependent_points")
    def module(
        context: sc.ModuleContext,
        qubit: _LogicalDeviceInput,
    ) -> sc.ProductRef:
        del qubit
        source = context._resource("source", requires=(_SCALAR_SIGNAL,))
        signal = context._product("signal", unit="ratio")
        context._acquire(
            "read-signal",
            resource=source,
            results={_SCALAR_SIGNAL_VALUE: signal},
        )
        return signal

    drive_length = sc.coordinate("drive_length", _QUANTITY_VALUE)

    @sc.experiment(
        id="test.runtime_entity_dependent_points",
        kind="runtime_entity_dependent_points",
    )
    def experiment(
        experiment: sc.ExperimentContext,
        qubit: _LogicalDeviceInput,
    ) -> None:
        signal = experiment.use(module(qubit=qubit))
        experiment.grid(
            sc.axis(
                drive_length,
                center=sc.parameter_lookup(
                    "sample_qubits",
                    key={"qubit": sc.input_ref(qubit)},
                    column="rabi_length",
                    value_type=_QUANTITY_VALUE,
                ),
                span=Quantity(value=20.0, unit="ns"),
                points=3,
            ),
        )
        experiment.alias(signal)

    resolved = bind_invocation(experiment.build(qubit="q0"), config_profile=config)
    preview = materialized_effects_contract(
        resolved,
        resolved.environment.parameters,
        config=config,
    )

    assert [point.coordinates["drive_length"] for point in preview.points] == [
        Quantity(value=30.0, unit="ns"),
        Quantity(value=40.0, unit="ns"),
        Quantity(value=50.0, unit="ns"),
    ]


def test_elaboration_invocation_literals_bind_local_inputs() -> None:
    @sc.module(id="test.invocation_defaults.child")
    def child(
        context: sc.ModuleContext,
        drive_frequency: _QuantityInput,
    ) -> None:
        source = context._resource("source", requires=(_SET_FREQUENCY,))
        context._bind_property(
            source,
            _SET_FREQUENCY_VALUE,
            value=drive_frequency,
        )

    @sc.module(id="test.invocation_defaults.parent")
    def parent(context: sc.ModuleContext) -> None:
        context.use(
            child.instantiate(
                "defaults-child",
                drive_frequency=Quantity(value=5.0, unit="GHz"),
            )
        )

    assembly = compose_module(
        parent.definition,
    )

    assert "drive_frequency" not in assembly.inputs
    assert all(port.id != "drive_frequency" for port in assembly.input_ports)
    first_value = _logical_binding_expression(assembly, 0)
    assert isinstance(first_value, LiteralScalarExpr)
    assert first_value.value == Quantity(value=5.0, unit="GHz")


def test_elaboration_invocation_expressions_bind_local_inputs() -> None:
    @sc.module(id="test.invocation_override.child")
    def child(
        context: sc.ModuleContext,
        drive_frequency: _QuantityInput,
    ) -> None:
        source = context._resource("source", requires=(_SET_FREQUENCY,))
        context._bind_property(
            source,
            _SET_FREQUENCY_VALUE,
            value=drive_frequency,
        )

    @sc.module(id="test.invocation_expression.parent")
    def parent(
        context: sc.ModuleContext,
        drive_frequency: _QuantityInput,
    ) -> None:
        context.use(
            child.instantiate(
                "expression-child",
                drive_frequency=drive_frequency,
            )
        )

    @sc.experiment(id="test.invocation-expression", kind="expression")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(
            parent(
                drive_frequency=authoring.parameter(
                    "drive_frequency",
                    authoring.QuantityType(),
                )
            )
        )

    assembly = compile_invocation(experiment.build()).program.program

    assert "drive_frequency" not in assembly.inputs
    assert _logical_binding_expression(assembly, 0) == param(
        "drive_frequency",
        _QUANTITY_VALUE,
    )


def test_elaboration_defers_nested_expression_and_literal_bindings() -> None:
    value_type = authoring.ScalarType(authoring.FloatType())

    @sc.module(id="test.invocation_deferred.child")
    def child(
        context: sc.ModuleContext,
        child_value: _FloatInput,
        unused_parameter: _FloatInput,
        unused_point: _FloatInput,
    ) -> None:
        del unused_parameter, unused_point
        source = context._resource("source", requires=(_SET_OFFSET,))
        context._bind_property(
            source,
            _SET_OFFSET_VALUE,
            value=child_value,
        )

    @sc.module(id="test.invocation_deferred.parent")
    def parent(
        context: sc.ModuleContext,
        parent_value: _FloatInput,
        unused_parameter: _FloatInput,
        unused_point: _FloatInput,
    ) -> None:
        parent_ref = sc.input_ref(parent_value)
        context.use(
            child.instantiate(
                "deferred-child",
                child_value=parent_ref + 0.25,
                unused_parameter=unused_parameter,
                unused_point=unused_point,
            )
        )

    @sc.experiment(id="test.invocation-deferred", kind="deferred")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(
            parent.instantiate(
                "deferred-parent",
                parent_value=1.5,
                unused_parameter=authoring.parameter(
                    "unused_parameter",
                    value_type,
                ),
                unused_point=authoring.coordinate("unused_point", value_type),
            )
        )

    assembly = compile_invocation(experiment.build()).program.program

    expression = _logical_binding_expression(assembly, 0)
    assert evaluate_scalar(expression, EvalContext()) == 1.75
    assert assembly.parameter_contracts == ()
    assert assembly.point_dependencies == ()


def test_module_provenance_follows_only_reachable_input_bindings() -> None:
    value_type = authoring.ScalarType(authoring.FloatType())

    @sc.module(id="test.reachable-input-provenance")
    def module(
        context: sc.ModuleContext,
        used_parameter: _FloatInput,
        unused_parameter: _FloatInput,
        used_point: _FloatInput,
        unused_point: _FloatInput,
    ) -> None:
        del unused_parameter, unused_point
        source = context._resource(
            "source",
            requires=(_SET_OFFSET, _SET_GAIN),
        )
        context._bind_property(
            source,
            _SET_OFFSET_VALUE,
            value=used_parameter,
        )
        context._bind_property(
            source,
            _SET_GAIN_VALUE,
            value=used_point,
        )

    used_parameter = authoring.parameter("reachable_parameter", value_type)
    unused_parameter = authoring.parameter("phantom_parameter", value_type)
    used_point = authoring.coordinate("reachable_point", value_type)
    unused_point = authoring.coordinate("phantom_point", value_type)

    assembly = compose_module(
        module.definition,
        used_parameter=used_parameter,
        unused_parameter=unused_parameter,
        used_point=used_point,
        unused_point=unused_point,
    )

    assert assembly.parameter_contracts == internal_value_ref_parameter_contracts(
        used_parameter
    )
    assert assembly.point_dependencies == internal_value_ref_point_dependencies(
        used_point
    )


def test_elaboration_invocation_input_refs_bind_to_parent_inputs() -> None:
    @sc.module(id="test.invocation_parent_input.child")
    def child(
        context: sc.ModuleContext,
        drive_frequency: _QuantityInput,
    ) -> None:
        source = context._resource("source", requires=(_SET_FREQUENCY,))
        context._bind_property(
            source,
            _SET_FREQUENCY_VALUE,
            value=drive_frequency,
        )

    @sc.module(id="test.invocation_parent_input.parent")
    def parent(
        context: sc.ModuleContext,
        outer_frequency: _QuantityInput,
    ) -> None:
        context.use(
            child.instantiate(
                "parent-input-child",
                drive_frequency=outer_frequency,
            )
        )

    assembly = compose_module(
        parent.definition, outer_frequency=Quantity(value=5.2, unit="GHz")
    )

    assert "drive_frequency" not in assembly.inputs
    localized = _logical_binding_expression(assembly, 0)
    assert isinstance(localized, InputScalarExpr)
    assert localized.name == "outer_frequency"


def test_elaboration_does_not_merge_sibling_invocation_inputs() -> None:
    @sc.module(id="test.invocation_sibling.first")
    def first(
        context: sc.ModuleContext,
        drive_frequency: _QuantityInput,
    ) -> None:
        source = context._resource("source", requires=(_SET_FREQUENCY,))
        context._bind_property(
            source,
            _SET_FREQUENCY_VALUE,
            value=drive_frequency,
        )

    @sc.module(id="test.invocation_sibling.second")
    def second(
        context: sc.ModuleContext,
        drive_frequency: _QuantityInput,
    ) -> None:
        detector = context._resource("detector", requires=(_SET_FREQUENCY,))
        context._bind_property(
            detector,
            _SET_FREQUENCY_VALUE,
            value=drive_frequency,
        )

    @sc.module(id="test.invocation_sibling.parent")
    def module(context: sc.ModuleContext) -> None:
        context.use(
            first.instantiate(
                "first",
                drive_frequency=Quantity(value=5.0, unit="GHz"),
            )
        )
        context.use(
            second.instantiate(
                "second",
                drive_frequency=Quantity(value=5.1, unit="GHz"),
            )
        )

    assembly = compose_module(
        module.definition,
    )

    assert "drive_frequency" not in assembly.inputs
    first_value = _logical_binding_expression(assembly, 0)
    second_value = _logical_binding_expression(assembly, 1)
    assert isinstance(first_value, LiteralScalarExpr)
    assert isinstance(second_value, LiteralScalarExpr)
    assert first_value.value == Quantity(value=5.0, unit="GHz")
    assert second_value.value == Quantity(value=5.1, unit="GHz")


def test_elaboration_localizes_invocation_entity_inputs() -> None:
    @sc.module(id="test.invocation_entity.child")
    def child(
        context: sc.ModuleContext,
        qubit: _EntityInput,
        drive_frequency: _QuantityInput,
    ) -> None:
        drive = context._resource(
            "drive",
            requires=(_SET_FREQUENCY,),
            for_entities=(sc.input_ref(qubit),),
        )
        context._bind_property(
            drive,
            _SET_FREQUENCY_VALUE,
            value=drive_frequency,
        )

    @sc.module(id="test.invocation_entity.parent")
    def parent(context: sc.ModuleContext) -> None:
        context.use(
            child.instantiate(
                "entity-child",
                qubit="q0",
                drive_frequency=Quantity(value=5.0, unit="GHz"),
            )
        )

    assembly = compose_module(
        parent.definition,
    )

    assert "qubit" not in assembly.inputs
    assert "qubit" not in assembly.entity_inputs
    assert all(port.id != "qubit" for port in assembly.input_ports)
    localized_entity = assembly.resource_ports[0].selector.entity_inputs[0]
    assert isinstance(localized_entity, ValueRef)
    assert localized_entity.value_type == authoring.ScalarType(authoring.EntityType())
    lowered_entity = internal_lower_scalar_value_ref(localized_entity)
    assert isinstance(lowered_entity, LiteralScalarExpr)
    assert lowered_entity.value == EntityRef(id="q0")


def test_experiment_invocation_runs_composed_modules_directly() -> None:
    @sc.module(id="test.scripted_module_prelude")
    def prelude(context: sc.ModuleContext) -> None:
        del context

    @sc.module(id="test.scripted_module_scan")
    def scan(
        context: sc.ModuleContext,
        drive_frequency: _QuantityInput,
    ) -> sc.ProductRef:
        source = context._resource(
            "source",
            requires=(_SET_FREQUENCY, _SCALAR_SIGNAL),
        )
        context._bind_property(
            source,
            _SET_FREQUENCY_VALUE,
            value=drive_frequency,
        )
        signal = context._product("signal", unit="ratio")
        context._acquire(
            "read-products",
            resource=source,
            results={_SCALAR_SIGNAL_VALUE: signal},
        )
        return signal

    @sc.experiment(id="test.scripted_scan", kind="simple_scan")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(prelude())
        signal = experiment.use(scan(DRIVE_FREQUENCY_POINT))
        experiment.alias(signal)

    resolved = bind_invocation(
        experiment.build().with_axis(
            sc.axis(
                DRIVE_FREQUENCY_POINT,
                center=sc.parameter("drive_frequency", _QUANTITY_VALUE),
                span=Quantity(value=200.0, unit="MHz"),
                points=5,
            )
        ),
        config_profile=load_config(),
    )
    preview = materialized_effects_contract(
        resolved,
        resolved.environment.parameters,
    )

    assert resolved.program.experiment_id == "test.scripted_scan"
    assert preview.points[0].coordinates["drive_frequency"] == Quantity(
        value=4.9, unit="GHz"
    )
    assert preview.points[-1].coordinates["drive_frequency"] == Quantity(
        value=5.1, unit="GHz"
    )


def test_resource_port_can_select_by_fixed_entity_input() -> None:
    seed_config = load_config()
    source_0 = seed_config.instrument_registry.instruments[0]
    topology = seed_config.topology.model_copy(
        update={
            "entities": [
                *seed_config.topology.entities,
                EntityRef(id="q1", kind="logical_device"),
            ],
        }
    )
    system = seed_config.system.model_copy(
        update={
            "topology": topology,
            "instrument_registry": seed_config.instrument_registry.model_copy(
                update={
                    "instruments": [
                        source_0,
                        source_0.model_copy(
                            update={
                                "id": "source-1",
                                "exclusivity_key": "source-1",
                            }
                        ),
                    ]
                }
            ),
            "routing": routing_graph(
                bindings=[
                    routing_endpoint(
                        instrument_id="source-0",
                        interface_id="test.set_frequency/v1",
                        entity_id="q0",
                        channel_id="drive-q0",
                    ),
                    routing_endpoint(
                        instrument_id="source-1",
                        interface_id="test.set_frequency/v1",
                        entity_id="q1",
                        channel_id="drive-q1",
                    ),
                ],
            ),
        }
    )
    config = seed_config.model_copy(update={"system": system})

    @sc.module(id="test.entity_selected_resource")
    def module(
        context: sc.ModuleContext,
        qubit: _EntityInput,
        drive_frequency: _QuantityInput,
    ) -> None:
        drive = context._resource(
            "drive",
            requires=(_SET_FREQUENCY,),
            for_entities=(sc.input_ref(qubit),),
        )
        context._bind_property(
            drive,
            _SET_FREQUENCY_VALUE,
            value=drive_frequency,
        )

    @sc.experiment(
        id="test.entity_selected_resource",
        kind="entity_selected_resource",
    )
    def experiment(
        experiment: sc.ExperimentContext,
        qubit: _EntityInput,
    ) -> None:
        experiment.use(
            module(
                qubit=qubit,
                drive_frequency=authoring.parameter(
                    "drive_frequency",
                    authoring.QuantityType(unit="GHz"),
                ),
            )
        )

    resolved = bind_invocation(
        experiment.build(qubit="q1"),
        config_profile=config,
    )

    preview = materialized_effects_contract(
        resolved,
        resolved.environment.parameters,
        config=config,
    )
    assert materialized_state_properties(preview)[0][1].instrument_id == "source-1"


def test_explicit_config_binds_experiment() -> None:
    selected_instrument = "spare-awg"
    config = config_with_physical_resources(
        {selected_instrument: ("test.drive_frequency/v1",)}
    )

    @sc.module(id="test.explicit-config-source")
    def module(context: sc.ModuleContext) -> None:
        drive = context._resource("drive", requires=(_DRIVE_FREQUENCY,))
        context._bind_property(
            drive,
            _DRIVE_FREQUENCY_VALUE,
            value=Quantity(value=5.0, unit="GHz"),
        )

    @sc.experiment(id="test.explicit-config-source", kind="config-source")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(module())

    resolved = bind_invocation(experiment.build(), config_profile=config)
    preview = materialized_effects_contract(
        resolved,
        resolved.environment.parameters,
        config=config,
    )

    [(_point_index, operation, _target)] = materialized_state_properties(preview)
    assert operation.instrument_id == selected_instrument
    assert resolved.environment.config is config
