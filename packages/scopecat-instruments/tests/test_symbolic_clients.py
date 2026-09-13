from __future__ import annotations

from typing import assert_type, cast

import pytest
from scopecat.authoring import (
    EntityType,
    ExperimentContext,
    IntType,
    ModuleContext,
    PerEntity,
    ProductRef,
    RecordedProducts,
    RecordRef,
    coordinate,
    each,
    experiment,
    module,
    one,
)
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.quantity import Quantity
from scopecat.program.bindings import EnsureStateIntent, InvocationIntent
from scopecat.program.expressions import LiteralScalarExpr
from scopecat.program.measurement_types import MeasurementArrayData
from scopecat.program.module import ModuleAcquireEffect
from scopecat.program.products import EntityRecordSelection, RecordSelection
from scopecat.program.recording import ProgramRecordSelection

from scopecat_instruments import (
    DCMonitorCurrentProducts,
    DCMonitorVoltageProducts,
    DCSourceGroupTarget,
    DCSourceMonitorGroupTarget,
    DCSourceMonitorTarget,
    DCSourceTarget,
    NetworkSweepProducts,
    NetworkSweepTarget,
    RFOutputGroupTarget,
    RFOutputTarget,
    SymbolicDCSourceClient,
    SymbolicDCSourceGroup,
    SymbolicDCSourceMonitorClient,
    SymbolicDCSourceMonitorGroup,
    SymbolicNetworkSweepClient,
    SymbolicNetworkSweepGroup,
    SymbolicRFOutputClient,
    SymbolicRFOutputGroup,
    SymbolicTemperatureReadoutClient,
    SymbolicTemperatureReadoutGroup,
    TemperatureSampleProducts,
    dc_source,
    dc_source_monitor,
    network_sweep,
    rf_output,
    temperature_readout,
)
from scopecat_instruments.members import (
    DC_MONITOR,
    DC_SOURCE,
    DC_SOURCE_OUTPUT_ENABLED,
    DC_SOURCE_VOLTAGE,
    NETWORK_SWEEP,
    RF_OUTPUT,
    TEMPERATURE_READOUT,
)


def _product_records(
    selections: tuple[ProgramRecordSelection, ...],
) -> tuple[RecordSelection, ...]:
    records = tuple(
        selection for selection in selections if isinstance(selection, RecordSelection)
    )
    assert len(records) == len(selections)
    return records


def test_factories_bind_typed_symbolic_clients_and_declare_resources() -> None:
    context = ModuleContext()
    qubit = coordinate("qubit", EntityType(entity_kind="qubit"))

    source = dc_source(context, for_=one(qubit))
    rf = rf_output(context)
    vna = network_sweep(context)
    thermometer = temperature_readout(context)

    assert_type(source, SymbolicDCSourceClient)
    assert_type(rf, SymbolicRFOutputClient)
    assert_type(vna, SymbolicNetworkSweepClient)
    assert_type(thermometer, SymbolicTemperatureReadoutClient)
    interface, _, _ = context.close_experiment_parts_internal()
    assert [resource.id for resource in interface.resources] == [
        "dc_source",
        "rf_output",
        "network_sweep",
        "temperature_readout",
    ]
    assert [resource.selector.interfaces for resource in interface.resources] == [
        (DC_SOURCE.interface_id,),
        (RF_OUTPUT.interface_id,),
        (NETWORK_SWEEP.interface_id,),
        (TEMPERATURE_READOUT.interface_id,),
    ]
    assert [resource.selector.capabilities for resource in interface.resources] == [
        (DC_SOURCE,),
        (RF_OUTPUT,),
        (NETWORK_SWEEP,),
        (TEMPERATURE_READOUT,),
    ]
    assert interface.resources[0].selector.entity_inputs == (qubit,)


def test_generated_symbolic_client_preserves_exact_resource_requirements() -> None:
    @experiment(id="test.symbolic.exact-requirements", kind="symbolic_root")
    def authored(context: ExperimentContext) -> None:
        source = dc_source(
            context,
            name="drive.source",
            requires=(DC_SOURCE_OUTPUT_ENABLED, DC_SOURCE_VOLTAGE),
        )
        assert_type(source, SymbolicDCSourceClient)
        source.ensure(output_enabled=True)
        source.source_voltage(
            range=Quantity(1.0, "V"),
            level=Quantity(0.05, "V"),
        )

    invocation = authored.build()
    [resource] = invocation.definition.interface.resources
    assert resource.id == "drive.source"
    assert resource.selector.capabilities == (
        DC_SOURCE_OUTPUT_ENABLED,
        DC_SOURCE_VOLTAGE,
    )
    assert resource.selector.interfaces == (DC_SOURCE.interface_id,)
    compile_invocation(invocation)


def test_generated_group_forwards_exact_requirements_to_each_resource() -> None:
    context = ModuleContext()
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")

    sources = dc_source(
        context,
        for_=each(q0, q1),
        requires=(DC_SOURCE_OUTPUT_ENABLED,),
    )
    sources.ensure(output_enabled=False)

    interface, _, _ = context.close_experiment_parts_internal()
    assert [resource.selector.capabilities for resource in interface.resources] == [
        (DC_SOURCE_OUTPUT_ENABLED,),
        (DC_SOURCE_OUTPUT_ENABLED,),
    ]


def test_generated_client_use_outside_exact_requirements_fails_verification() -> None:
    @experiment(id="test.symbolic.insufficient-requirements", kind="symbolic_root")
    def authored(context: ExperimentContext) -> None:
        source = dc_source(
            context,
            requires=(DC_SOURCE_OUTPUT_ENABLED,),
        )
        source.source_voltage(
            range=Quantity(1.0, "V"),
            level=Quantity(0.05, "V"),
        )

    with pytest.raises(CheckFailed) as error:
        compile_invocation(authored.build())

    assert [problem.code for problem in error.value.problems] == [
        "module_resource_port_capability_missing",
    ]


def test_explicit_dc_source_factories_cover_scalar_and_group() -> None:
    context = ModuleContext()
    q0 = EntityRef(id="q0", kind="logical_device")
    selection = each(q0)

    scalar = dc_source(context)
    scalar_monitor = dc_source_monitor(context)
    group = dc_source(context, for_=selection)
    group_monitor = dc_source_monitor(
        context,
        for_=selection,
    )

    assert_type(scalar, SymbolicDCSourceClient)
    assert_type(scalar_monitor, SymbolicDCSourceMonitorClient)
    assert_type(group, SymbolicDCSourceGroup)
    assert_type(group_monitor, SymbolicDCSourceMonitorGroup)

    interface, _, _ = context.close_experiment_parts_internal()
    assert [resource.selector.interfaces for resource in interface.resources] == [
        (DC_SOURCE.interface_id,),
        (DC_SOURCE.interface_id, DC_MONITOR.interface_id),
        (DC_SOURCE.interface_id,),
        (DC_SOURCE.interface_id, DC_MONITOR.interface_id),
    ]


def test_one_concrete_entity_lowers_to_one_literal_entity_input() -> None:
    context = ModuleContext()
    q0 = EntityRef(id="q0", kind="logical_device")

    source = dc_source(context, for_=one(q0))

    assert_type(source, SymbolicDCSourceClient)
    interface, _, _ = context.close_experiment_parts_internal()
    [entity_input] = interface.resources[0].selector.entity_inputs
    assert isinstance(entity_input.source, LiteralScalarExpr)
    assert entity_input.source.value == q0


def test_each_expands_into_single_entity_resources_and_unique_acquisitions() -> None:
    context = ModuleContext()
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")

    analyzers = network_sweep(context, for_=each(q0, q1))
    assert_type(analyzers, SymbolicNetworkSweepGroup)
    assert analyzers.entities == (q0, q1)
    assert analyzers[q0] is analyzers.clients[q0]
    analyzers.ensure(points=11)
    traces = analyzers.sweep()

    assert_type(traces, PerEntity[NetworkSweepProducts])
    definition = context.close_definition_internal(id="test.symbolic.each")
    resources = definition.interface.resources
    assert len(resources) == 2
    assert all(
        resource.id.startswith("network_sweep.logical-device-q")
        for resource in resources
    )
    assert len({resource.id for resource in resources}) == 2
    for resource, entity in zip(resources, (q0, q1), strict=True):
        [entity_input] = resource.selector.entity_inputs
        assert isinstance(entity_input.source, LiteralScalarExpr)
        assert entity_input.source.value == entity

    acquisitions = tuple(
        effect
        for effect in definition.body.effects
        if isinstance(effect, ModuleAcquireEffect)
    )
    assert len(acquisitions) == 2
    assert len({effect.id for effect in acquisitions}) == 2
    assert len(definition.body.products) == 4
    assert len({product.qualified_id for product in definition.body.products}) == 4
    for entity in (q0, q1):
        trace = traces[entity]
        assert trace.frequency.id.startswith(analyzers[entity].id)
        assert trace.s_parameter.id.startswith(analyzers[entity].id)


def test_group_sweep_uses_entity_aligned_output_shape_state() -> None:
    context = ModuleContext()
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")
    analyzers = network_sweep(context, for_=each(q0, q1))
    points_by_entity: PerEntity[int] = PerEntity(
        (
            (q1, 17),
            (q0, 5),
        )
    )

    analyzers.ensure(points=points_by_entity)
    traces = analyzers.sweep()

    definition = context.close_definition_internal(id="test.symbolic.each-shape")
    products = {product.qualified_id: product for product in definition.body.products}
    for entity, points in ((q0, 5), (q1, 17)):
        trace = traces[entity]
        assert products[trace.frequency.id].axes[0].size == points
        assert products[trace.s_parameter.id].axes[0].size == points


def test_each_factories_keep_the_typed_interface_specific_group_verbs() -> None:
    context = ModuleContext()
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")
    selection = each(q0, q1)

    outputs = rf_output(context, for_=selection)
    thermometers = temperature_readout(context, for_=selection)
    assert_type(outputs, SymbolicRFOutputGroup)
    assert_type(thermometers, SymbolicTemperatureReadoutGroup)
    outputs.ensure(RFOutputGroupTarget(output_enabled=False))
    samples = thermometers.sample()

    assert_type(samples, PerEntity[TemperatureSampleProducts])
    assert tuple(samples) == (q0, q1)


def test_dc_group_broadcasts_and_aligns_typed_operation_arguments() -> None:
    context = ModuleContext()
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")
    biases = dc_source(context, for_=each(q0, q1))
    assert_type(biases, SymbolicDCSourceGroup)
    biases.source_voltage(
        range=Quantity(1, "V"),
        level=Quantity(0.01, "V"),
    )
    current_ranges = assert_type(
        PerEntity(
            (
                (q1, Quantity(2, "A")),
                (q0, Quantity(1, "A")),
            )
        ),
        PerEntity[Quantity],
    )
    current_levels = assert_type(
        PerEntity(
            (
                (q1, Quantity(0.03, "A")),
                (q0, Quantity(0.02, "A")),
            )
        ),
        PerEntity[Quantity],
    )
    biases.source_current(range=current_ranges, level=current_levels)

    definition = context.close_definition_internal(id="test.symbolic.dc-each")
    invocations = tuple(
        effect
        for effect in definition.body.effects
        if isinstance(effect, InvocationIntent)
    )
    assert [effect.operation_id for effect in invocations] == [
        "source_voltage",
        "source_voltage",
        "source_current",
        "source_current",
    ]
    assert [
        [argument.value for argument in effect.arguments] for effect in invocations
    ] == [
        [Quantity(1, "V"), Quantity(0.01, "V")],
        [Quantity(1, "V"), Quantity(0.01, "V")],
        [Quantity(1, "A"), Quantity(0.02, "A")],
        [Quantity(2, "A"), Quantity(0.03, "A")],
    ]


def test_generated_rf_group_aligns_state_and_success_state() -> None:
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")

    @experiment(id="test.symbolic.generated-rf-state", kind="symbolic_root")
    def authored(context: ExperimentContext) -> None:
        outputs = rf_output(context, for_=each(q0, q1))
        assert_type(outputs, SymbolicRFOutputGroup)
        outputs.ensure(
            PerEntity(
                (
                    (q1, RFOutputTarget(output_enabled=True)),
                    (q0, RFOutputTarget(output_enabled=False)),
                )
            )
        )
        context.on_success(outputs, RFOutputGroupTarget(output_enabled=False))

    definition = authored.bind().definition
    ensures = tuple(
        effect
        for effect in definition.body.effects
        if isinstance(effect, EnsureStateIntent)
    )
    assert len(ensures) == 1
    assert [assignment.value for assignment in ensures[0].assignments] == [False, True]
    assert definition.success_state is not None
    assert [
        assignment.value for assignment in definition.success_state.assignments
    ] == [
        False,
        False,
    ]


def test_group_target_lifts_each_field_independently() -> None:
    context = ModuleContext()
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")
    outputs = rf_output(context, for_=each(q0, q1))

    outputs.ensure(
        RFOutputGroupTarget(
            power=Quantity(-20, "dBm"),
            output_enabled=PerEntity(((q1, True), (q0, False))),
        )
    )

    definition = context.close_definition_internal(id="test.symbolic.field-lift")
    ensures = tuple(
        effect
        for effect in definition.body.effects
        if isinstance(effect, EnsureStateIntent)
    )
    assert len(ensures) == 1
    assert [assignment.value for assignment in ensures[0].assignments] == [
        Quantity(-20, "dBm"),
        False,
        Quantity(-20, "dBm"),
        True,
    ]


def test_group_per_entity_operation_argument_requires_exact_identity_join() -> None:
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")
    wrong_q1 = EntityRef(id="q1", kind="logical_coupler")
    biases = dc_source(ModuleContext(), for_=each(q0, q1))
    levels = PerEntity(
        (
            (q0, Quantity(0.01, "V")),
            (wrong_q1, Quantity(0.02, "V")),
        )
    )

    with pytest.raises(
        ValueError,
        match=(
            r"exactly match.*missing logical_device:q1; "
            r"extra logical_coupler:q1"
        ),
    ):
        biases.source_voltage(range=Quantity(1, "V"), level=levels)


def test_symbolic_products_record_directly_from_a_root_experiment() -> None:
    @experiment(id="test.symbolic.root", kind="symbolic_root")
    def authored(context: ExperimentContext) -> None:
        vna = network_sweep(context)
        vna.ensure(points=11)
        trace = vna.sweep()
        records = assert_type(context.alias(trace), RecordedProducts)
        assert isinstance(records.frequency, RecordRef)
        assert isinstance(records.s_parameter, RecordRef)

    definition = authored.bind().definition
    assert [product.id for product in definition.body.products] == [
        "frequency",
        "s_parameter",
    ]
    selections = _product_records(definition.record_selections)
    assert [selection.product_id.qualified_name for selection in selections] == [
        "network_sweep.sweep/frequency",
        "network_sweep.sweep/s_parameter",
    ]
    assert [selection.record_id for selection in selections] == [
        "network_sweep.sweep/frequency",
        "network_sweep.sweep/s_parameter",
    ]
    assert [selection.role for selection in selections] == [
        "coordinate",
        "observable",
    ]
    assert {selection.recording_group_id for selection in selections} == {
        "network_sweep.sweep"
    }


def test_record_namespace_prefixes_typed_variables_and_their_group() -> None:
    context = ExperimentContext()
    vna = network_sweep(context)
    vna.ensure(points=11)

    context.alias(vna.sweep(), namespace="calibration")

    definition = context.close_definition_internal(
        id="test.symbolic.record-namespace",
        kind="test",
        metadata=None,
        input_defaults={},
        required_inputs=(),
    )
    selections = _product_records(definition.record_selections)
    assert [selection.record_id for selection in selections] == [
        "calibration/network_sweep.sweep/frequency",
        "calibration/network_sweep.sweep/s_parameter",
    ]
    assert {selection.recording_group_id for selection in selections} == {
        "calibration/network_sweep.sweep"
    }


def test_typed_result_recording_semantics_survive_a_module_boundary() -> None:
    @module(id="test.symbolic.sweep-module")
    def sweep_module(context: ModuleContext) -> NetworkSweepProducts:
        vna = network_sweep(context)
        vna.ensure(points=11)
        return vna.sweep()

    call = sweep_module.instantiate("segment")
    frequency = call.result.frequency

    context = ExperimentContext()
    context.use(call)
    context.alias(frequency)
    definition = context.close_definition_internal(
        id="test.symbolic.record-module-member",
        kind="test",
        metadata=None,
        input_defaults={},
        required_inputs=(),
    )
    selections = _product_records(definition.record_selections)
    assert [selection.role for selection in selections] == ["coordinate"]
    assert [selection.recording_group_id for selection in selections] == [
        "segment/network_sweep.sweep"
    ]


def test_per_entity_symbolic_results_explicitly_stack_as_entity_variables() -> None:
    context = ExperimentContext()
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")
    analyzers = network_sweep(context, for_=each(q0, q1))
    analyzers.ensure(points=5)

    traces = analyzers.sweep()
    assert_type(traces, PerEntity[NetworkSweepProducts])
    context.stack_entities(
        traces.map(lambda result: result.frequency),
        record_id="calibration/frequency",
    )
    context.stack_entities(
        traces.map(lambda result: result.s_parameter),
        record_id="calibration/s_parameter",
    )

    definition = context.close_definition_internal(
        id="test.symbolic.record-each",
        kind="test",
        metadata=None,
        input_defaults={},
        required_inputs=(),
    )
    selections = definition.record_selections
    assert all(isinstance(selection, EntityRecordSelection) for selection in selections)
    grouped = tuple(
        cast("EntityRecordSelection", selection) for selection in selections
    )
    assert [selection.role for selection in grouped] == ["coordinate", "observable"]
    assert [selection.record_id for selection in grouped] == [
        "calibration/frequency",
        "calibration/s_parameter",
    ]
    assert all(selection.axis.values == (q0, q1) for selection in grouped)


def test_returned_group_bundle_records_one_variable_per_field() -> None:
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")

    @experiment(id="test.symbolic.return-each", kind="test")
    def definition(
        context: ExperimentContext,
    ) -> PerEntity[NetworkSweepProducts]:
        analyzers = network_sweep(context, for_=each(q0, q1))
        analyzers.ensure(points=5)
        return analyzers.sweep()

    invocation = definition.build()
    selections = invocation.definition.record_selections
    assert all(isinstance(selection, EntityRecordSelection) for selection in selections)
    grouped = tuple(
        cast("EntityRecordSelection", selection) for selection in selections
    )
    assert [selection.record_id for selection in grouped] == [
        "frequency",
        "s_parameter",
    ]
    assert all(selection.axis.id == "logical_device" for selection in grouped)
    assert all(selection.axis.values == (q0, q1) for selection in grouped)
    assert {selection.recording_group_id for selection in grouped} == {"result"}
    assert isinstance(invocation.output, PerEntity)
    frequency = invocation.entity_result_ref("frequency")
    signal = invocation.entity_result_ref("s_parameter")
    assert frequency.dims[0:2] == ("point", "logical_device")
    assert signal.dims[0:2] == ("point", "logical_device")


def test_on_success_accepts_a_typed_symbolic_client_and_declared_state() -> None:
    @experiment(id="test.symbolic.typed-success-state", kind="symbolic_root")
    def authored(context: ExperimentContext) -> None:
        source = dc_source(context)
        context.on_success(source, DCSourceTarget(output_enabled=False))

    definition = authored.bind().definition
    assert definition.success_state is not None
    [assignment] = definition.success_state.assignments
    [resource] = definition.interface.resources
    assert assignment.port_id == resource.symbol_id
    assert assignment.property_id == "output_enabled"
    assert assignment.value is False


@pytest.mark.parametrize("per_entity", [False, True])
def test_on_success_accepts_group_broadcast_and_per_entity_state(
    *,
    per_entity: bool,
) -> None:
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")

    @experiment(
        id=f"test.symbolic.typed-group-success-state.{per_entity}",
        kind="symbolic_root",
    )
    def authored(context: ExperimentContext) -> None:
        sources = dc_source(context, for_=each(q0, q1))
        target: DCSourceGroupTarget | PerEntity[DCSourceTarget]
        if per_entity:
            target = PerEntity(
                (
                    (q1, DCSourceTarget(output_enabled=True)),
                    (q0, DCSourceTarget(output_enabled=False)),
                )
            )
        else:
            target = DCSourceGroupTarget(output_enabled=False)
        context.on_success(sources, target)

    definition = authored.bind().definition
    assert definition.success_state is not None
    assert [
        assignment.port_id for assignment in definition.success_state.assignments
    ] == [resource.symbol_id for resource in definition.interface.resources]
    assert [
        assignment.value for assignment in definition.success_state.assignments
    ] == ([False, True] if per_entity else [False, False])


def test_two_scalar_clients_use_distinct_structured_product_scopes() -> None:
    context = ModuleContext()
    left = network_sweep(context)
    right = network_sweep(context)
    left.ensure(NetworkSweepTarget(points=3))
    right.ensure(NetworkSweepTarget(points=3))

    left_trace = left.sweep()
    right_trace = right.sweep()

    definition = context.close_definition_internal(id="test.symbolic.scopes")
    assert len({product.qualified_id for product in definition.body.products}) == 4
    assert left_trace.s_parameter.id == "network_sweep.sweep/s_parameter"
    assert right_trace.s_parameter.id == "network_sweep.sweep.2/s_parameter"


def test_unused_client_does_not_rename_later_acquisition_products() -> None:
    direct_context = ModuleContext()
    direct = network_sweep(direct_context)
    direct.ensure(points=3)
    direct_trace = direct.sweep()

    context_with_unused_client = ModuleContext()
    network_sweep(context_with_unused_client)
    selected = network_sweep(context_with_unused_client)
    selected.ensure(points=3)
    selected_trace = selected.sweep()

    assert selected_trace.frequency.id == direct_trace.frequency.id
    assert selected_trace.s_parameter.id == direct_trace.s_parameter.id


def test_state_clients_record_typed_state_and_operation_effects() -> None:
    context = ModuleContext()
    source = dc_source(context)
    rf = rf_output(context)

    source.ensure(
        DCSourceTarget(
            current_protection=Quantity(100.0, "uA"),
            output_enabled=True,
        )
    )
    source.source_voltage(
        range=Quantity(1.0, "V"),
        level=Quantity(0.05, "V"),
    )
    rf.ensure(
        RFOutputTarget(
            frequency=Quantity(5.0, "GHz"),
            power=Quantity(-20.0, "dBm"),
            output_enabled=True,
        )
    )

    definition = context.close_definition_internal(id="test.symbolic.state")
    assert len(definition.body.effects) == 3
    assert isinstance(definition.body.effects[0], EnsureStateIntent)
    assert isinstance(definition.body.effects[1], InvocationIntent)
    assert isinstance(definition.body.effects[2], EnsureStateIntent)
    assert [
        len(effect.assignments)
        for effect in definition.body.effects
        if isinstance(effect, EnsureStateIntent)
    ] == [2, 3]
    [invocation] = definition.body.invocations
    assert invocation.operation_id == "source_voltage"
    assert [argument.value for argument in invocation.arguments] == [
        Quantity(1.0, "V"),
        Quantity(0.05, "V"),
    ]


def test_dc_monitor_exposes_independent_fixed_result_acquisitions() -> None:
    context = ModuleContext()
    source = dc_source_monitor(context)
    assert_type(source, SymbolicDCSourceMonitorClient)
    source.ensure(
        DCSourceMonitorTarget(
            output_enabled=True,
            measurement_enabled=True,
        )
    )
    source.source_voltage(
        range=Quantity(1.0, "V"),
        level=Quantity(0.05, "V"),
    )

    current = source.measure_current()
    voltage = source.measure_voltage()

    assert_type(current, DCMonitorCurrentProducts)
    assert_type(current.current, ProductRef[float])
    assert_type(voltage, DCMonitorVoltageProducts)
    assert_type(voltage.voltage, ProductRef[float])
    definition = context.close_definition_internal(id="test.symbolic.dc-monitor")
    assert [product.id for product in definition.body.products] == [
        "monitored_current",
        "monitored_voltage",
    ]
    acquisitions = definition.body.effects[-2:]
    assert all(isinstance(effect, ModuleAcquireEffect) for effect in acquisitions)
    assert [
        [result.result_id for result in effect.results]
        for effect in acquisitions
        if isinstance(effect, ModuleAcquireEffect)
    ] == [["monitored_current"], ["monitored_voltage"]]


def test_dc_monitor_acquisition_does_not_require_symbolic_source_state() -> None:
    source = dc_source_monitor(ModuleContext())

    assert_type(source.measure_current(), DCMonitorCurrentProducts)
    assert_type(source.measure_voltage(), DCMonitorVoltageProducts)


def test_dc_monitor_selection_is_a_static_symbolic_capability_boundary() -> None:
    context = ModuleContext()
    source = dc_source(context)
    monitor = dc_source_monitor(context)

    assert_type(source, SymbolicDCSourceClient)
    assert_type(monitor, SymbolicDCSourceMonitorClient)
    assert not hasattr(source, "measure_current")
    assert not hasattr(source, "measure_voltage")
    assert hasattr(monitor, "measure_current")
    assert hasattr(monitor, "measure_voltage")

    interface, _, _ = context.close_experiment_parts_internal()
    assert interface.resources[0].selector.interfaces == (DC_SOURCE.interface_id,)
    assert interface.resources[1].selector.interfaces == (
        DC_SOURCE.interface_id,
        DC_MONITOR.interface_id,
    )


def test_dc_monitor_group_selection_retains_monitor_verbs() -> None:
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")

    sources = dc_source_monitor(
        ModuleContext(),
        for_=each(q0, q1),
    )

    assert_type(sources, SymbolicDCSourceMonitorGroup)
    assert_type(sources[q0], SymbolicDCSourceMonitorClient)
    assert hasattr(sources, "measure_current")
    assert hasattr(sources, "measure_voltage")


def test_dc_monitor_group_maps_each_fixed_acquisition_per_entity() -> None:
    context = ModuleContext()
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")
    sources = dc_source_monitor(
        context,
        for_=each(q0, q1),
    )
    sources.source_voltage(
        range=PerEntity(
            (
                (q1, Quantity(2.0, "V")),
                (q0, Quantity(1.0, "V")),
            )
        ),
        level=PerEntity(
            (
                (q1, Quantity(0.02, "V")),
                (q0, Quantity(0.03, "V")),
            )
        ),
    )
    sources.ensure(DCSourceMonitorGroupTarget(measurement_enabled=True))

    current_samples = sources.measure_current()
    voltage_samples = sources.measure_voltage()

    assert_type(current_samples, PerEntity[DCMonitorCurrentProducts])
    assert_type(voltage_samples, PerEntity[DCMonitorVoltageProducts])
    assert isinstance(current_samples[q0].current, ProductRef)
    assert isinstance(current_samples[q1].current, ProductRef)
    assert isinstance(voltage_samples[q0].voltage, ProductRef)
    assert isinstance(voltage_samples[q1].voltage, ProductRef)
    definition = context.close_definition_internal(
        id="test.symbolic.dc-monitor-each-acquisitions"
    )
    assert [product.id for product in definition.body.products] == [
        "monitored_current",
        "monitored_current",
        "monitored_voltage",
        "monitored_voltage",
    ]


def test_network_sweep_declares_contract_products_and_ensured_points() -> None:
    context = ModuleContext()
    vna = network_sweep(context)
    vna.ensure(
        NetworkSweepTarget(
            start_frequency=Quantity(4.9, "GHz"),
            stop_frequency=Quantity(5.1, "GHz"),
            points=337,
            s_parameter="S21",
        )
    )

    trace = vna.sweep()

    assert_type(trace, NetworkSweepProducts)
    assert_type(trace.frequency, ProductRef[MeasurementArrayData])
    assert_type(trace.s_parameter, ProductRef[MeasurementArrayData])
    definition = context.close_definition_internal(id="test.symbolic.sweep")
    assert [product.id for product in definition.body.products] == [
        "frequency",
        "s_parameter",
    ]
    assert [product.dtype for product in definition.body.products] == [
        "float64",
        "complex128",
    ]
    assert [product.unit for product in definition.body.products] == ["Hz", "ratio"]
    assert [product.axes[0].size for product in definition.body.products] == [337, 337]
    assert [product.axes[0].kind for product in definition.body.products] == [
        "frequency",
        "frequency",
    ]
    assert [product.axes[0].unit for product in definition.body.products] == [
        "Hz",
        "Hz",
    ]
    assert len({product.axes[0].shared_as for product in definition.body.products}) == 1
    assert definition.body.products[0].axes[0].shared_as == "frequency"
    acquisition = definition.body.effects[-1]
    assert isinstance(acquisition, ModuleAcquireEffect)
    assert acquisition.id == "network_sweep.sweep"
    assert acquisition.acquisition_id == "sweep"
    assert [result.result_id for result in acquisition.results] == [
        "frequency",
        "s_parameter",
    ]
    assert [result.product for result in acquisition.results] == [
        trace.frequency,
        trace.s_parameter,
    ]


def test_network_sweep_rejects_point_varying_output_shape() -> None:
    context = ModuleContext()
    points = coordinate("points", IntType())
    vna = network_sweep(context)
    vna.ensure(points=points)

    with pytest.raises(
        ValueError,
        match=r"output-shaping state.*configuration binding.*point execution",
    ):
        vna.sweep(id="second")


def test_explicit_acquisition_id_namespaces_the_scoped_local_products() -> None:
    context = ModuleContext()
    vna = network_sweep(context)
    vna.ensure(NetworkSweepTarget(points=5))

    trace = vna.sweep(id="second")

    definition = context.close_definition_internal(id="test.symbolic.second")
    products = {product.qualified_id: product for product in definition.body.products}
    assert trace.frequency.id == "network_sweep.second/frequency"
    assert trace.s_parameter.id == "network_sweep.second/s_parameter"
    assert products[trace.frequency.id].axes[0].shared_as == "frequency"


def test_explicit_acquisition_ids_are_unique_durable_namespaces() -> None:
    context = ModuleContext()
    first = network_sweep(context)
    second = network_sweep(context)
    first.ensure(points=3)
    second.ensure(points=3)

    first.sweep(id="readout")
    with pytest.raises(ValueError, match="duplicate explicit effect id"):
        second.sweep(id="readout")


def test_network_sweep_requires_ensured_state_sized_axis() -> None:
    context = ModuleContext()
    vna = network_sweep(context)

    with pytest.raises(
        ValueError,
        match=r"axis 'frequency'.*ensure that state",
    ):
        vna.sweep()


def test_temperature_sample_declares_all_contract_products() -> None:
    context = ModuleContext()
    thermometer = temperature_readout(context)

    sample = thermometer.sample()

    assert_type(sample, TemperatureSampleProducts)
    assert_type(sample.temperature, ProductRef[float])
    assert_type(sample.resistance, ProductRef[float])
    definition = context.close_definition_internal(id="test.symbolic.temperature")
    assert [product.id for product in definition.body.products] == [
        "temperature",
        "resistance",
    ]
    assert [product.dtype for product in definition.body.products] == [
        "float64",
        "float64",
    ]
    assert [product.unit for product in definition.body.products] == ["K", "Ohm"]
    assert all(not product.axes for product in definition.body.products)
    [acquisition] = definition.body.effects
    assert isinstance(acquisition, ModuleAcquireEffect)
    assert [result.product for result in acquisition.results] == [
        sample.temperature,
        sample.resistance,
    ]
