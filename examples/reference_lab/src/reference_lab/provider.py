"""One provider for every virtual instrument in the reference laboratory."""

from __future__ import annotations

from typing import cast

import scopecat as sc
from pydantic import JsonValue
from scopecat.records.instrument import CommandChannelBinding
from scopecat.records.measurement import (
    MeasurementAcquisitionValue,
    MeasurementScalar,
)
from scopecat.sdk.instruments import (
    AcquisitionRef,
    AcquisitionResultRef,
    DriverAcquisition,
    DriverCatalog,
    DriverConnectionSpec,
    DriverOperation,
    DriverOutcome,
    DriverReadback,
    DriverScalar,
    DriverSpec,
    DriverStateObservation,
    DriverStatePatch,
    DriverStateReadback,
    DriverStateReadRequest,
    DriverSuccess,
    InstrumentConnectionContext,
    InstrumentDescription,
    InstrumentDriver,
    InstrumentProviderContext,
    InstrumentProviderDescription,
    OperationRef,
    PropertyRef,
    instrument_component,
    interface_mount,
)
from scopecat_instruments import ConfiguredInstrumentProvider
from scopecat_instruments.interfaces import (
    dc_bias_interface,
    dc_monitor_interface,
    dc_source_interface,
)
from scopecat_instruments.members import (
    DC_BIAS_ACTUAL_VOLTAGE,
    DC_BIAS_RAMP_DURATION,
    DC_BIAS_READBACK,
    DC_BIAS_SETTLE_TOLERANCE,
    DC_BIAS_SETTLED,
    DC_BIAS_TARGET_VOLTAGE,
    DC_SOURCE_OUTPUT_ENABLED,
)
from scopecat_instruments.virtual import VirtualDcSource, VirtualLabWorld

from reference_lab.bench_devices import (
    VIRTUAL_AWG_DRIVER_ID,
    VIRTUAL_AWG_DRIVER_SPEC,
    VIRTUAL_DIGITIZER_DRIVER_ID,
    VIRTUAL_DIGITIZER_DRIVER_SPEC,
    VIRTUAL_TIMING_CONTROLLER_DRIVER_ID,
    VIRTUAL_TIMING_CONTROLLER_DRIVER_SPEC,
    BenchSignalWorld,
    VirtualAwg,
    VirtualDigitizer,
    VirtualTimingController,
)

FLUX_SOURCE_IDS = ("flux-dac-a", "flux-dac-b")
FLUX_SOURCE_ID = FLUX_SOURCE_IDS[0]
FLUX_CHANNEL_COMPONENT_IDS = ("ch1", "ch2")
MULTICHANNEL_DC_DRIVER_ID = "reference_lab.virtual.multichannel_dc_source"
MULTICHANNEL_DC_DRIVER_SPEC = DriverSpec(
    driver_id=MULTICHANNEL_DC_DRIVER_ID,
    implementation_version="v1",
    label="Virtual two-channel DC source",
    connections=(
        DriverConnectionSpec(
            kind="virtual",
            options_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
    ),
)


class ReferenceLabProvider:
    """Combine stock instruments and the lab's bare virtual devices."""

    provider_id = "reference_lab.virtual_lab.provider"

    def __init__(self, *, seed: int = 7) -> None:
        self._instruments = ConfiguredInstrumentProvider(seed=seed)
        world = self._instruments.world
        if world is None:
            raise AssertionError("stock provider must create its virtual world")
        self._world = world
        self._bench = BenchSignalWorld()
        self.driver_catalog = DriverCatalog(
            provider_id=self.provider_id,
            drivers=(
                *self._instruments.driver_catalog.drivers,
                MULTICHANNEL_DC_DRIVER_SPEC,
                VIRTUAL_AWG_DRIVER_SPEC,
                VIRTUAL_DIGITIZER_DRIVER_SPEC,
                VIRTUAL_TIMING_CONTROLLER_DRIVER_SPEC,
            ),
        )

    @property
    def world(self) -> VirtualLabWorld:
        """Expose the coupled instrument world for gallery verification."""

        return self._world

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        instrument_driver_ids = {
            spec.driver_id for spec in self._instruments.driver_catalog.drivers
        }
        instrument_bindings = tuple(
            binding
            for binding in context.bindings
            if binding.driver_id in instrument_driver_ids
        )
        stock_description = self._instruments.describe(
            InstrumentProviderContext(instrument_bindings)
        )
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=(
                *stock_description.instruments,
                *tuple(
                    MultiChannelVirtualDcSource(
                        binding.id,
                        self.world,
                    ).describe()
                    for binding in context.bindings
                    if binding.driver_id == MULTICHANNEL_DC_DRIVER_ID
                ),
                *tuple(
                    VirtualAwg(
                        binding.id,
                        self._bench,
                        output_count=_channel_count(
                            binding.connection.options,
                            "output_count",
                            default=8,
                        ),
                    ).describe()
                    for binding in context.bindings
                    if binding.driver_id == VIRTUAL_AWG_DRIVER_ID
                ),
                *tuple(
                    VirtualDigitizer(
                        binding.id,
                        self._bench,
                        input_count=_channel_count(
                            binding.connection.options,
                            "input_count",
                            default=2,
                        ),
                    ).describe()
                    for binding in context.bindings
                    if binding.driver_id == VIRTUAL_DIGITIZER_DRIVER_ID
                ),
                *tuple(
                    VirtualTimingController(binding.id, self._bench).describe()
                    for binding in context.bindings
                    if binding.driver_id == VIRTUAL_TIMING_CONTROLLER_DRIVER_ID
                ),
            ),
            problems=stock_description.problems,
        )

    def connect(self, context: InstrumentConnectionContext) -> InstrumentDriver:
        instrument_driver_ids = {
            spec.driver_id for spec in self._instruments.driver_catalog.drivers
        }
        if context.binding.driver_id in instrument_driver_ids:
            return self._instruments.connect(context)
        if context.binding.driver_id == MULTICHANNEL_DC_DRIVER_ID:
            return MultiChannelVirtualDcSource(
                context.binding.id,
                self.world,
            )
        if context.binding.driver_id == VIRTUAL_AWG_DRIVER_ID:
            return VirtualAwg(
                context.binding.id,
                self._bench,
                output_count=_channel_count(
                    context.binding.connection.options,
                    "output_count",
                    default=8,
                ),
            )
        if context.binding.driver_id == VIRTUAL_DIGITIZER_DRIVER_ID:
            return VirtualDigitizer(
                context.binding.id,
                self._bench,
                input_count=_channel_count(
                    context.binding.connection.options,
                    "input_count",
                    default=2,
                ),
            )
        if context.binding.driver_id == VIRTUAL_TIMING_CONTROLLER_DRIVER_ID:
            return VirtualTimingController(context.binding.id, self._bench)
        msg = f"unsupported reference-lab driver {context.binding.driver_id!r}"
        raise ValueError(msg)


def _channel_count(
    options: dict[str, JsonValue],
    key: str,
    *,
    default: int,
) -> int:
    return cast("int", options.get(key, default))


class MultiChannelVirtualDcSource:
    """One physical DC source with independently routed virtual channels."""

    implementation_id = MULTICHANNEL_DC_DRIVER_ID
    implementation_version = "v1"

    def __init__(
        self,
        instrument_id: str,
        world: VirtualLabWorld,
    ) -> None:
        self.instrument_id = instrument_id
        self._world = world
        self._unrouted = VirtualDcSource(f"{instrument_id}:unrouted", world)
        self._drivers: dict[str, VirtualDcSource] = {}
        self._bindings: dict[
            str,
            dict[tuple[str, str, str | None], CommandChannelBinding],
        ] = {}
        self._route_ids: dict[str, str] = {}
        self._ramp_duration_s: dict[str, float] = {}
        self._settle_tolerance_v: dict[str, float] = {}

    def describe(self) -> InstrumentDescription:
        interfaces = (
            dc_bias_interface(),
            dc_source_interface(),
            dc_monitor_interface(),
        )
        return InstrumentDescription(
            instrument_id=self.instrument_id,
            implementation_id=self.implementation_id,
            implementation_version=self.implementation_version,
            label="Virtual multi-channel DC source",
            description=(
                "Independently routed DC source/monitor channels in one physical "
                "instrument."
            ),
            components=[
                instrument_component(
                    "channels",
                    components=[
                        instrument_component(channel_id)
                        for channel_id in FLUX_CHANNEL_COMPONENT_IDS
                    ],
                )
            ],
            interfaces=list(interfaces),
            interface_mounts=[
                interface_mount(interface_spec.id, "channels", channel_id)
                for interface_spec in interfaces
                for channel_id in FLUX_CHANNEL_COMPONENT_IDS
            ],
        )

    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        requested_by_component: dict[str, list[PropertyRef]] = {}
        for target in request.targets:
            if not isinstance(target, PropertyRef):
                raise ValueError("virtual DC source has no model-specific members")
            component_id = self._component_id(target, ())
            requested_by_component.setdefault(component_id, []).append(target)

        observations: list[DriverStateObservation] = []
        metadata: dict[str, JsonValue] = {
            "mode": "virtual",
            "channel_count": len(FLUX_CHANNEL_COMPONENT_IDS),
        }
        bias_targets = {
            DC_BIAS_TARGET_VOLTAGE,
            DC_BIAS_RAMP_DURATION,
            DC_BIAS_SETTLE_TOLERANCE,
            DC_BIAS_ACTUAL_VOLTAGE,
            DC_BIAS_SETTLED,
        }
        for component_id, requested in requested_by_component.items():
            component_path = ("channels", component_id)
            driver = self._drivers.get(component_id, self._unrouted)
            bindings = tuple(self._bindings.get(component_id, {}).values())
            root_targets = frozenset(
                _root_property(target)
                for target in requested
                if _root_property(target) not in bias_targets
            )
            if root_targets:
                readback = driver.read_state(DriverStateReadRequest(root_targets))
                for observation in readback.observations:
                    assert isinstance(observation.target, PropertyRef)
                    observations.append(
                        DriverStateObservation(
                            target=_mount_property(observation.target, component_path),
                            value=observation.value,
                            source=observation.source,
                            coherence_id=observation.coherence_id,
                            metadata=observation.metadata,
                            entity_ids=tuple(
                                dict.fromkeys(binding.entity_id for binding in bindings)
                            ),
                            channel_bindings=tuple(
                                binding.model_copy(
                                    update={
                                        "interface_id": observation.target.interface_id
                                    }
                                )
                                for binding in bindings
                            ),
                        )
                    )

            route_id = self._route_ids.get(component_id)
            source = (
                self._world.dc_source(f"{self.instrument_id}:{route_id}")
                if route_id is not None
                else None
            )
            bias_values = {
                DC_BIAS_TARGET_VOLTAGE: sc.Quantity(
                    source.voltage_level_v if source is not None else 0.0, "V"
                ),
                DC_BIAS_RAMP_DURATION: sc.Quantity(
                    self._ramp_duration_s.get(component_id, 0.0), "s"
                ),
                DC_BIAS_SETTLE_TOLERANCE: sc.Quantity(
                    self._settle_tolerance_v.get(component_id, 0.0001), "V"
                ),
                DC_BIAS_ACTUAL_VOLTAGE: sc.Quantity(
                    source.voltage_level_v if source is not None else 0.0, "V"
                ),
                DC_BIAS_SETTLED: True,
            }
            observations.extend(
                DriverStateObservation(
                    target=target,
                    value=bias_values[_root_property(target)],
                    entity_ids=tuple(
                        dict.fromkeys(binding.entity_id for binding in bindings)
                    ),
                    channel_bindings=tuple(
                        binding.model_copy(update={"interface_id": target.interface_id})
                        for binding in bindings
                    ),
                )
                for target in requested
                if _root_property(target) in bias_values
            )
        return DriverStateReadback(
            observations=tuple(observations)
        ).with_observation_metadata(metadata)

    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        grouped: dict[str, list[tuple[PropertyRef, DriverScalar]]] = {}
        for entry in request.entries:
            if not isinstance(entry.target, PropertyRef):
                raise ValueError("virtual DC source has no model-specific members")
            component_id = self._component_id(
                entry.target,
                entry.channel_bindings,
            )
            grouped.setdefault(component_id, []).append(
                (_root_property(entry.target), entry.value)
            )
        component_results: dict[str, JsonValue] = {}
        for component_id, entries in grouped.items():
            bias_values = {
                target: value
                for target, value in entries
                if target.interface_id == "scopecat.dc_bias/v1"
            }
            if bias_values:
                duration = bias_values.get(DC_BIAS_RAMP_DURATION)
                tolerance = bias_values.get(DC_BIAS_SETTLE_TOLERANCE)
                target = bias_values.get(DC_BIAS_TARGET_VOLTAGE)
                if isinstance(duration, sc.Quantity):
                    self._ramp_duration_s[component_id] = duration.to("s").value
                if isinstance(tolerance, sc.Quantity):
                    self._settle_tolerance_v[component_id] = tolerance.to("V").value
                if isinstance(target, sc.Quantity):
                    outcome = self._drivers[component_id].source_voltage(
                        range=sc.Quantity(1.0, "V"),
                        level=target,
                    )
                    if not isinstance(outcome, DriverSuccess):
                        return outcome
                route_id = self._route_ids[component_id]
                source = self._world.dc_source(f"{self.instrument_id}:{route_id}")
                component_results[f"channels/{component_id}"] = {
                    "status": "settled",
                    "actual_voltage_v": source.voltage_level_v,
                }
            source_entries = tuple(
                (target, value)
                for target, value in entries
                if target.interface_id != "scopecat.dc_bias/v1"
            )
            if not source_entries:
                continue
            outcome = self._drivers[component_id].apply_state(
                DriverStatePatch(values=dict(source_entries))
            )
            if not isinstance(outcome, DriverSuccess):
                return outcome
        return DriverSuccess(
            None,
            metadata=(
                {"component_results": component_results} if component_results else {}
            ),
        )

    def invoke(
        self,
        request: DriverOperation,
    ) -> DriverOutcome[DriverStateReadback | None]:
        component_id = self._component_id(
            request.target,
            request.channel_bindings,
        )
        outcome = self._drivers[component_id].invoke(
            DriverOperation(
                target=_root_operation(request.target),
                arguments=request.arguments,
            )
        )
        if isinstance(outcome, DriverSuccess):
            return DriverSuccess(None, metadata=outcome.metadata)
        return outcome

    def collect(self, request: DriverAcquisition) -> DriverOutcome[DriverReadback]:
        component_id = self._component_id(
            request.target,
            request.channel_bindings,
        )
        if (
            request.target.interface_id == DC_BIAS_READBACK.interface_id
            and request.target.acquisition_id == DC_BIAS_READBACK.acquisition_id
        ):
            readback_spec = next(
                acquisition
                for acquisition in dc_bias_interface().acquisitions
                if acquisition.id == DC_BIAS_READBACK.acquisition_id
            )
            specs_by_id = {result.id: result for result in readback_spec.results}
            targets_by_result: dict[AcquisitionResultRef, PropertyRef] = {}
            for result in request.results:
                source_property = specs_by_id[result.result_id].source_property
                if source_property is None:
                    raise AssertionError(
                        "member observation result requires a source property"
                    )
                targets_by_result[result] = PropertyRef(
                    source_property.interface_id,
                    request.target.component_path
                    + tuple(source_property.component_path),
                    source_property.property_id,
                )
            state = self.read_state(
                DriverStateReadRequest(frozenset(targets_by_result.values()))
            )
            observations = {
                observation.target: observation for observation in state.observations
            }
            values: dict[AcquisitionResultRef, MeasurementAcquisitionValue] = {}
            evidence: dict[str, JsonValue] = {}
            for result, target in targets_by_result.items():
                result_spec = specs_by_id[result.result_id]
                observation = observations[target]
                value = observation.value
                if isinstance(value, sc.Quantity):
                    if result_spec.unit is None:
                        raise AssertionError(
                            "quantity member observation result requires a unit"
                        )
                    value = value.to(result_spec.unit).value
                values[result] = MeasurementScalar.create(
                    dtype=result_spec.dtype,
                    unit=result_spec.unit,
                    value=value,
                    metadata=observation.metadata,
                )
                evidence[result.result_id] = {
                    "source": observation.source,
                    "coherence_id": observation.coherence_id,
                }
            return DriverSuccess(
                DriverReadback(
                    values=values,
                    metadata={
                        "component_path": ["channels", component_id],
                        "state_observations": evidence,
                    },
                )
            )
        root_results = frozenset(_root_result(result) for result in request.results)
        outcome = self._drivers[component_id].collect(
            DriverAcquisition(
                target=_root_acquisition(request.target),
                results=root_results,
                dimensions={
                    _root_result(result): request.dimensions.get(result, ())
                    for result in request.results
                },
            )
        )
        if not isinstance(outcome, DriverSuccess):
            return outcome
        requested_results = {result.result_id: result for result in request.results}
        return DriverSuccess(
            DriverReadback(
                values={
                    requested_results[result.result_id]: value
                    for result, value in outcome.value.values.items()
                },
                metadata=outcome.value.metadata,
            ),
            metadata=outcome.metadata,
        )

    def disconnect(self) -> None:
        for driver in self._drivers.values():
            driver.disconnect()

    def abort(self) -> None:
        for driver in self._drivers.values():
            outcome = driver.apply_state(
                DriverStatePatch(values={DC_SOURCE_OUTPUT_ENABLED: False})
            )
            if not isinstance(outcome, DriverSuccess):
                raise RuntimeError(
                    f"{self.instrument_id} did not confirm channel bias-off"
                )
            driver.abort()

    def _component_id(
        self,
        target: PropertyRef | OperationRef | AcquisitionRef,
        bindings: tuple[CommandChannelBinding, ...],
    ) -> str:
        component_path = target.component_path
        if (
            len(component_path) != 2
            or component_path[0] != "channels"
            or component_path[1] not in FLUX_CHANNEL_COMPONENT_IDS
        ):
            raise ValueError(
                f"{self.instrument_id} requires a concrete channels/<id> target"
            )
        component_id = component_path[1]
        relevant_bindings = tuple(
            binding
            for binding in bindings
            if binding.interface_id in {None, target.interface_id}
        )
        known_bindings = self._bindings.setdefault(component_id, {})
        for binding in relevant_bindings:
            known_bindings.setdefault(
                (binding.entity_id, binding.channel_id, binding.interface_id),
                binding,
            )
        if component_id not in self._route_ids:
            route_id = (
                relevant_bindings[0].channel_id
                if relevant_bindings
                else f"component.{component_id}"
            )
            self._route_ids[component_id] = route_id
            self._drivers[component_id] = VirtualDcSource(
                f"{self.instrument_id}:{route_id}",
                self._world,
            )
        return component_id


def _root_property(target: PropertyRef) -> PropertyRef:
    return PropertyRef(target.interface_id, (), target.property_id)


def _mount_property(
    target: PropertyRef,
    component_path: tuple[str, ...],
) -> PropertyRef:
    return PropertyRef(target.interface_id, component_path, target.property_id)


def _root_operation(target: OperationRef) -> OperationRef:
    return OperationRef(target.interface_id, (), target.operation_id)


def _root_acquisition(target: AcquisitionRef) -> AcquisitionRef:
    return AcquisitionRef(target.interface_id, (), target.acquisition_id)


def _root_result(target: AcquisitionResultRef) -> AcquisitionResultRef:
    return AcquisitionResultRef(
        target.interface_id,
        (),
        target.acquisition_id,
        target.result_id,
    )


__all__ = ["FLUX_SOURCE_ID", "FLUX_SOURCE_IDS", "ReferenceLabProvider"]
