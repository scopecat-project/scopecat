"""Project accepted system routing into demo quantum target endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from scopecat.planning.routing import endpoint_entity_ids
from scopecat.records.config import ConfigProfileSnapshot
from scopecat_instruments.members import RF_OUTPUT
from scopecat_quantum._ids import QubitId
from scopecat_quantum.pulses import (
    AcquireSignal,
    DriveSignal,
    ReadoutSignal,
)

from reference_lab.bench_interfaces import (
    ANALOG_WAVEFORM_OUTPUT,
    DIGITIZER_INPUT,
)

LIST_MODE_TARGET_KIND = "reference_lab.list-mode"
DRIVE_I_ROLE = "drive-i"
DRIVE_Q_ROLE = "drive-q"
READOUT_I_ROLE = "readout-i"
READOUT_Q_ROLE = "readout-q"
READOUT_ACQUISITION_ROLE = "readout-acquisition"
DRIVE_LO_ROLE = "drive-lo"
READOUT_LO_ROLE = "readout-lo"


@dataclass(frozen=True, slots=True)
class ConfiguredQuantumRoute:
    """One complete logical-signal route owned by the selected target."""

    instrument_id: str
    role_id: str | None
    interface_id: str
    entity_id: str
    entity_kind: str
    channel_id: str
    component_path: tuple[str, ...]

    @property
    def endpoint_id(self) -> str:
        return f"{self.instrument_id}:{self.channel_id}"


@dataclass(frozen=True, slots=True)
class ConfiguredRfOutput:
    """One statically distributed RF output selected by its routing route."""

    group_id: str
    instrument_id: str
    signal: Literal["drive", "readout"]
    entity_ids: tuple[str, ...]
    component_path: tuple[str, ...]


def configured_quantum_routes(
    config: ConfigProfileSnapshot,
    *,
    target_kind: str,
) -> tuple[str, tuple[ConfiguredQuantumRoute, ...]]:
    """Select one target and normalize its static routing from configuration."""

    target = config.domain_target
    if target is None:
        raise ValueError("quantum configuration requires a domain target")
    if target.kind != target_kind:
        raise ValueError(
            f"configured target kind {target.kind!r} is not {target_kind!r}"
        )
    instrument_ids = set(target.instrument_ids)
    routes: list[ConfiguredQuantumRoute] = []
    for resource_route in config.routing.routes:
        if resource_route.instrument_id not in instrument_ids:
            continue
        for endpoint in resource_route.endpoints:
            if endpoint.channel_id is None:
                continue
            for entity_id in endpoint_entity_ids(resource_route, endpoint):
                entity = config.topology.entity(entity_id)
                if entity is None or entity.kind is None:
                    raise ValueError(
                        f"quantum route requires a typed entity {entity_id!r}"
                    )
                routes.append(
                    ConfiguredQuantumRoute(
                        instrument_id=resource_route.instrument_id,
                        role_id=resource_route.role_id,
                        interface_id=endpoint.interface_id,
                        entity_id=entity_id,
                        entity_kind=entity.kind,
                        channel_id=endpoint.channel_id,
                        component_path=tuple(endpoint.component_path),
                    )
                )
    if not routes:
        raise ValueError("configured quantum target has no routed endpoints")
    return target.id, tuple(routes)


def configured_rf_outputs(
    config: ConfigProfileSnapshot,
    *,
    target_kind: str,
) -> tuple[ConfiguredRfOutput, ...]:
    """Resolve LO distribution groups from RF-output routing routes."""

    target = config.domain_target
    if target is None or target.kind != target_kind:
        raise ValueError(f"configured target kind is not {target_kind!r}")
    outputs: list[ConfiguredRfOutput] = []
    for route in config.routing.routes:
        signal = (
            "drive"
            if route.role_id == DRIVE_LO_ROLE
            else "readout"
            if route.role_id == READOUT_LO_ROLE
            else None
        )
        if signal is None:
            continue
        endpoints = tuple(
            endpoint
            for endpoint in route.endpoints
            if endpoint.interface_id == RF_OUTPUT.interface_id
        )
        entity_ids = tuple(sorted(route.entity_ids))
        component_paths = {endpoint.component_path for endpoint in endpoints}
        if not entity_ids or len(component_paths) != 1:
            raise ValueError(
                f"RF output route {route.id!r} requires one physical component "
                "shared by at least one entity"
            )
        [component_path] = component_paths
        outputs.append(
            ConfiguredRfOutput(
                group_id=route.id,
                instrument_id=route.instrument_id,
                signal=signal,
                entity_ids=entity_ids,
                component_path=component_path,
            )
        )
    if not outputs:
        raise ValueError("configured quantum target has no routed RF outputs")
    return tuple(outputs)


def configured_output_signal(
    route: ConfiguredQuantumRoute,
) -> DriveSignal | ReadoutSignal | None:
    """Project one configured route into a logical pulse-output signal."""

    if route.entity_kind == "logical_qubit":
        qubit = QubitId(route.entity_id)
        if (
            route.interface_id == ANALOG_WAVEFORM_OUTPUT.interface_id
            and route.role_id in {DRIVE_I_ROLE, DRIVE_Q_ROLE}
        ):
            return DriveSignal(qubit)
        if (
            route.interface_id == ANALOG_WAVEFORM_OUTPUT.interface_id
            and route.role_id in {READOUT_I_ROLE, READOUT_Q_ROLE}
        ):
            return ReadoutSignal(qubit)
    return None


def configured_acquisition_signal(
    route: ConfiguredQuantumRoute,
) -> AcquireSignal | None:
    """Project one configured route into a logical acquisition signal."""

    if (
        route.interface_id == DIGITIZER_INPUT.interface_id
        and route.role_id == READOUT_ACQUISITION_ROLE
        and route.entity_kind == "logical_qubit"
    ):
        return AcquireSignal(QubitId(route.entity_id))
    return None


__all__ = [
    "DRIVE_I_ROLE",
    "DRIVE_LO_ROLE",
    "DRIVE_Q_ROLE",
    "LIST_MODE_TARGET_KIND",
    "READOUT_ACQUISITION_ROLE",
    "READOUT_I_ROLE",
    "READOUT_LO_ROLE",
    "READOUT_Q_ROLE",
    "ConfiguredQuantumRoute",
    "ConfiguredRfOutput",
    "configured_acquisition_signal",
    "configured_output_signal",
    "configured_quantum_routes",
    "configured_rf_outputs",
]
