# pyright: reportUnusedFunction=false

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

import numpy as np
from numpy.typing import NDArray

import scopecat as sc
from scopecat.authoring._module_context import DefinitionResource
from scopecat.authoring.member_projection import StateTarget
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.measurements.results import MeasurementValue
from scopecat.program.logical import (
    AcquireEffect,
    LogicalEnsureState,
    LogicalInvocation,
    LogicalStateAssignment,
)
from scopecat.program.state import StateBinding
from scopecat.sdk.instruments import InterfaceRef, PropertyRef

_DEVICE = InterfaceRef("test.experiment_context_device/v1")
_DEVICE_LEVEL = _DEVICE.property("level")
_DEVICE_ENABLED = _DEVICE.property("enabled")
_DEVICE_MODE = _DEVICE.property("mode")
_DEVICE_TRIGGER = _DEVICE.operation("trigger")
_DEVICE_TRIGGER_LEVEL = _DEVICE_TRIGGER.argument("level")
_DEVICE_SIGNAL = _DEVICE.acquisition("sample").result("signal")
_LEVEL_TYPE = sc.FloatType()


@dataclass(frozen=True)
class _DeviceTarget:
    level: StateBinding
    enabled: StateBinding


def _device_assignments(
    state: _DeviceTarget,
    /,
) -> Mapping[PropertyRef, StateBinding]:
    return {
        _DEVICE_LEVEL: state.level,
        _DEVICE_ENABLED: state.enabled,
    }


@dataclass(frozen=True)
class _TypedDevice:
    resource: DefinitionResource

    def state_targets(
        self,
        state: _DeviceTarget,
        /,
    ) -> tuple[StateTarget, ...]:
        return ((self.resource, _device_assignments(state)),)


def _build_trigger(*, level: object) -> object:
    return {"level": level}


def _derive_signal(value: MeasurementValue) -> dict[str, MeasurementValue]:
    return {"derived": value}


def test_experiment_authors_root_device_operations_without_a_module() -> None:
    @sc.experiment(id="test.experiment-context.direct", kind="direct")
    def direct(
        experiment: sc.ExperimentContext,
        level: Annotated[sc.Input[float], _LEVEL_TYPE] = 1.5,
    ) -> None:
        device = experiment._resource("device", requires=(_DEVICE,))
        trigger_payload = experiment.compute(
            "build-trigger",
            fn=_build_trigger,
            inputs={"level": level},
            output_type=sc.ScalarType(sc.PayloadType("trigger")),
        )
        experiment._ensure(
            device,
            _device_assignments(_DeviceTarget(level=level, enabled=True)),
        )
        experiment._bind_property(device, _DEVICE_MODE, value="measurement")
        experiment._invoke(
            "trigger",
            resource=device,
            operation=_DEVICE_TRIGGER,
            arguments={_DEVICE_TRIGGER_LEVEL: trigger_payload},
        )
        raw = experiment._product("raw", metadata={"stage": "capture"})
        derived = experiment._product("derived")
        experiment._acquire(
            "read-signal",
            resource=device,
            results={_DEVICE_SIGNAL: raw},
            metadata={"mode": "fast"},
        )
        experiment._measurement_compute(
            "derive",
            input=raw,
            outputs={"derived": derived},
            kernel=_derive_signal,
        )
        experiment.alias(raw)
        experiment.alias(derived)
        experiment.on_success(
            _TypedDevice(device),
            _DeviceTarget(level=0.0, enabled=False),
        )

    invocation = direct.build()
    root_resources = invocation.definition.interface.resources
    assert [port.qualified_id for port in root_resources] == ["device"]

    logical = compile_invocation(invocation).program.program
    assert [product.qualified_id for product in logical.product_declarations] == [
        "raw",
        "derived",
    ]
    selected_products = [
        product_id.qualified_name
        for selection in logical.product_record_selections
        for product_id in selection.product_ids
    ]
    assert selected_products == [
        "raw",
        "derived",
    ]
    assert [node.id.local_id for node in logical.compute_nodes] == ["build-trigger"]
    compute_ids = [
        compute.id.qualified_name for compute in logical.measurement_computes
    ]
    assert compute_ids == ["derive"]
    assert [type(effect) for effect in logical.effects] == [
        LogicalEnsureState,
        LogicalStateAssignment,
        LogicalInvocation,
        AcquireEffect,
    ]
    [invocation_effect] = logical.invocations
    assert invocation_effect.port_id.qualified_name == "device"
    assert invocation_effect.arguments[0].id == "level"
    [acquisition] = logical.acquisitions
    assert acquisition.resource_port_id.qualified_name == "device"
    assert acquisition.results[0].metadata == {"mode": "fast"}
    assert isinstance(logical.success_state, LogicalEnsureState)
    assert [
        assignment.property_id for assignment in logical.success_state.assignments
    ] == ["level", "enabled"]


def test_experiment_supports_direct_root_authoring() -> None:
    @sc.experiment(id="test.direct", kind="direct")
    def direct(experiment: sc.ExperimentContext) -> None:
        device = experiment._resource("device", requires=(_DEVICE,))
        signal = experiment._product("signal")
        experiment._acquire(
            "read-signal",
            resource=device,
            results={_DEVICE_SIGNAL: signal},
        )
        experiment.alias(signal)

    program = compile_invocation(direct.build()).program.program

    assert [port.id for port in program.resource_ports] == ["device"]
    assert [product.qualified_id for product in program.product_declarations] == [
        "signal"
    ]
    assert [
        product_id.qualified_name
        for selection in program.product_record_selections
        for product_id in selection.product_ids
    ] == ["signal"]


def test_public_capability_resources_support_lab_owned_group_composition() -> None:
    q0 = sc.EntityRef(id="q0", kind="logical_device")
    q1 = sc.EntityRef(id="q1", kind="logical_device")
    entities = sc.each(q0, q1)

    @sc.experiment(id="test.capability-resource", kind="direct")
    def direct(experiment: sc.ExperimentContext) -> None:
        resources = sc.capability_resource(
            experiment,
            "drive",
            requires=(_DEVICE_LEVEL, _DEVICE_ENABLED),
            for_=entities,
            role="xy",
        )
        sc.ensure_state_targets(
            experiment,
            tuple(
                resources[entity].state_target(
                    {
                        _DEVICE_LEVEL: float(index),
                        _DEVICE_ENABLED: True,
                    }
                )
                for index, entity in enumerate(entities)
            ),
        )

    invocation = direct.build()
    assert [
        port.selector.capabilities for port in invocation.definition.interface.resources
    ] == [
        (_DEVICE_LEVEL, _DEVICE_ENABLED),
        (_DEVICE_LEVEL, _DEVICE_ENABLED),
    ]
    assert [
        port.selector.interfaces for port in invocation.definition.interface.resources
    ] == [(_DEVICE.interface_id,), (_DEVICE.interface_id,)]
    assert [
        (port.id, port.selector.role.role_id)
        for port in invocation.definition.interface.resources
    ] == [("drive.q0", "xy"), ("drive.q1", "xy")]
    logical = compile_invocation(invocation).program.program
    [effect] = logical.effects
    assert isinstance(effect, LogicalEnsureState)
    assert [
        (assignment.port_id.local_id, assignment.property_id)
        for assignment in effect.assignments
    ] == [
        ("drive.q0", "level"),
        ("drive.q0", "enabled"),
        ("drive.q1", "level"),
        ("drive.q1", "enabled"),
    ]


def test_public_capability_resource_supports_lab_owned_acquisition() -> None:
    @sc.experiment(id="test.capability-resource-acquisition", kind="direct")
    def direct(experiment: sc.ExperimentContext) -> None:
        monitor = sc.capability_resource(
            experiment,
            "monitor",
            requires=(_DEVICE_SIGNAL.acquisition,),
        )
        signal = experiment._product("signal")
        monitor.acquire({_DEVICE_SIGNAL: signal})
        experiment.alias(signal)

    logical = compile_invocation(direct.build()).program.program

    [acquisition] = logical.acquisitions
    assert acquisition.resource_port_id.local_id == "monitor"
    assert acquisition.acquisition_id == "sample"
    assert acquisition.results[0].result_id == "signal"


def test_public_capability_resource_supports_exact_operation_requirement() -> None:
    @sc.experiment(id="test.capability-resource-operation", kind="direct")
    def direct(experiment: sc.ExperimentContext) -> None:
        device = sc.capability_resource(
            experiment,
            "device",
            requires=(_DEVICE_TRIGGER,),
        )
        device.invoke(_DEVICE_TRIGGER)

    invocation = direct.build()
    [port] = invocation.definition.interface.resources
    assert port.selector.capabilities == (_DEVICE_TRIGGER,)

    logical = compile_invocation(invocation).program.program
    [operation] = logical.invocations
    assert operation.operation_id == "trigger"


def test_experiment_records_a_compute_result_as_a_named_dataset_value() -> None:
    @sc.experiment(id="test.direct.value-record", kind="direct")
    def direct(experiment: sc.ExperimentContext) -> None:
        score = experiment.compute(
            "score",
            fn=lambda: 2.5,
            output_type=sc.ScalarType(sc.FloatType()),
        )
        experiment.alias(score)

    logical = compile_invocation(direct.build()).program.program

    assert logical.product_record_selections == ()
    [record] = logical.value_record_selections
    assert record.id == "score"
    assert record.source_value_id == "score"
    assert record.value_id == logical.compute_nodes[0].result_id


def test_experiment_composes_and_records_array_compute_results() -> None:
    trace_type = sc.ArrayType(
        dtype="float64",
        dimensions=(sc.ArrayDimension("sample", 3, kind="time", unit="ns"),),
        unit="V",
    )

    def peak_value(*, values: NDArray[np.float64]) -> float:
        return float(np.max(values))

    @sc.experiment(id="test.direct.array-value-record", kind="direct")
    def direct(experiment: sc.ExperimentContext) -> None:
        trace = experiment.compute(
            "trace",
            fn=lambda: np.asarray([1.0, 2.0, 3.0]),
            output_type=trace_type,
        )
        peak = experiment.compute(
            "peak",
            fn=peak_value,
            inputs={"values": trace},
            output_type=sc.ScalarType(sc.FloatType()),
        )
        trace_record = experiment.alias(trace)
        experiment.alias(peak)
        assert trace_record.dims == ("point", "sample")

    logical = compile_invocation(direct.build()).program.program

    assert [node.id.local_id for node in logical.compute_nodes] == ["trace", "peak"]
    assert logical.compute_nodes[1].inputs[0][1] == logical.compute_nodes[0].result_id
    [trace_record, peak_record] = logical.value_record_selections
    assert isinstance(trace_record.value_type, sc.ArrayType)
    assert trace_record.value_type.dimensions[0].id == "sample"
    assert peak_record.value_type == sc.ScalarType(sc.FloatType())


def test_experiment_derives_record_id_from_module_source_identity() -> None:
    @sc.module(id="test.value_source")
    def value_source(module: sc.ModuleContext) -> sc.ValueRef:
        return module.compute(
            "score",
            fn=lambda: 2.5,
            output_type=sc.ScalarType(sc.FloatType()),
        )

    @sc.experiment(id="test.module-value-record", kind="direct")
    def direct(experiment: sc.ExperimentContext) -> None:
        score = experiment.use(value_source())
        trace = experiment._product("trace")
        experiment.alias(score)
        experiment.alias(trace)

    logical = compile_invocation(direct.build()).program.program

    [record] = logical.value_record_selections
    [trace] = logical.product_record_selections
    assert logical.record_selections == (record, trace)
    assert record.id == "value_source/score"
    assert record.source_value_id == "value_source/score"
    assert record.value_id == logical.compute_nodes[0].result_id


def test_value_record_namespaces_preserve_segment_identity() -> None:
    @sc.experiment(id="test.value-record.namespace", kind="direct")
    def direct(experiment: sc.ExperimentContext) -> None:
        score = experiment.compute(
            "score",
            fn=lambda: 2.5,
            output_type=sc.ScalarType(sc.FloatType()),
        )
        experiment.alias(score, namespace="analysis%2Fdaily")
        experiment.alias(score, namespace="analysis/daily")

    logical = compile_invocation(direct.build()).program.program

    assert [record.id for record in logical.value_record_selections] == [
        "analysis%2Fdaily/score",
        "analysis/daily/score",
    ]
