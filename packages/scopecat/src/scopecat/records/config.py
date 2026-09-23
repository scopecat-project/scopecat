"""Configuration lifecycle models."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.interface_identity import InterfaceId
from scopecat.kernel.state import StateValue
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.instrument import (
    InstrumentStateSetting,
    state_member_identity,
)
from scopecat.records.parameter import (
    ParameterCatalog,
    ParameterSnapshot,
)

type ConfigContentHash = Annotated[
    str,
    Field(pattern=r"^sha256:[0-9a-f]{64}$"),
]
type _NonEmptyId = Annotated[str, Field(min_length=1)]


class _HasId(Protocol):
    @property
    def id(self) -> str: ...


def _ensure_unique[T: _HasId](items: list[T], label: str) -> list[T]:
    seen: set[str] = set()
    for item in items:
        item_id = item.id
        if item_id in seen:
            msg = f"duplicate {label} id: {item_id}"
            raise ValueError(msg)
        seen.add(item_id)
    return items


def _exclude_empty(value: object) -> bool:
    return not value


def _exclude_best_effort(value: object) -> bool:
    return value == "best_effort"


def _default_best_effort() -> Literal["best_effort"]:
    return "best_effort"


class TopologyConnection(BaseModel):
    """One typed, undirected connection between two configured entities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: _NonEmptyId
    kind: _NonEmptyId
    endpoints: tuple[_NonEmptyId, _NonEmptyId]
    entity_id: _NonEmptyId | None = None

    @model_validator(mode="after")
    def validate_endpoints(self) -> TopologyConnection:
        if self.endpoints[0] == self.endpoints[1]:
            raise ValueError("topology connection endpoints must be distinct")
        return self


class Topology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[EntityRef] = Field(default_factory=list)
    connections: list[TopologyConnection] = Field(default_factory=list)

    @field_validator("entities")
    @classmethod
    def validate_entities(cls, value: list[EntityRef]) -> list[EntityRef]:
        return _ensure_unique(value, "entity")

    @field_validator("connections")
    @classmethod
    def validate_connection_ids(
        cls,
        value: list[TopologyConnection],
    ) -> list[TopologyConnection]:
        return _ensure_unique(value, "topology connection")

    @model_validator(mode="after")
    def validate_connection_entities(self) -> Topology:
        entity_ids = {entity.id for entity in self.entities}
        missing = sorted(
            endpoint
            for connection in self.connections
            for endpoint in connection.endpoints
            if endpoint not in entity_ids
        )
        missing.extend(
            connection.entity_id
            for connection in self.connections
            if connection.entity_id is not None
            and connection.entity_id not in entity_ids
        )
        if missing:
            raise ValueError(
                "topology connections reference unknown entities: "
                + ", ".join(dict.fromkeys(missing))
            )
        return self

    def entity(self, entity_id: str) -> EntityRef | None:
        for entity in self.entities:
            if entity.id == entity_id:
                return entity
        return None


class _InstrumentConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    options: dict[str, JsonValue] = Field(default_factory=dict)


class VirtualInstrumentConnection(_InstrumentConnection):
    kind: Literal["virtual"] = "virtual"


class DriverManagedInstrumentConnection(_InstrumentConnection):
    """Physical connection whose resources are owned by its driver factory."""

    kind: Literal["driver_managed"] = "driver_managed"


class TcpipSocketInstrumentConnection(_InstrumentConnection):
    kind: Literal["tcpip_socket"] = "tcpip_socket"
    host: Annotated[str, Field(min_length=1)]
    port: int = Field(ge=1, le=65535)
    timeout_seconds: float = Field(default=5.0, gt=0)


class SerialInstrumentConnection(_InstrumentConnection):
    """One locally attached serial port with explicit framing settings."""

    kind: Literal["serial"] = "serial"
    port: Annotated[str, Field(min_length=1)]
    baud_rate: int = Field(default=9600, ge=1)
    timeout_seconds: float = Field(default=1.0, gt=0)
    write_timeout_seconds: float = Field(default=1.0, gt=0)
    data_bits: Literal[5, 6, 7, 8] = 8
    parity: Literal["none", "even", "odd", "mark", "space"] = "none"
    stop_bits: float = 1.0
    xonxoff: bool = False
    rtscts: bool = False
    dsrdtr: bool = False

    @field_validator("stop_bits")
    @classmethod
    def validate_stop_bits(cls, value: float) -> float:
        if value not in {1.0, 1.5, 2.0}:
            raise ValueError("serial stop_bits must be 1, 1.5, or 2")
        return value


type InstrumentConnection = Annotated[
    (
        VirtualInstrumentConnection
        | DriverManagedInstrumentConnection
        | TcpipSocketInstrumentConnection
        | SerialInstrumentConnection
    ),
    Field(discriminator="kind"),
]


class InstrumentBindingSpec(BaseModel):
    """Provider-visible identity and connection for one configured instrument."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, Field(min_length=1)]
    driver_id: Annotated[str, Field(min_length=1)]
    connection: InstrumentConnection


type InstrumentRunStartPolicy = Literal["preserve", "apply_default_state"]
type InstrumentSuccessAction = Literal[
    "release",
    "restore_baseline",
    "apply_safe_state",
]
type InstrumentFailureAction = Literal[
    "abort_and_release",
    "abort_then_safe_state",
]
type InstrumentSafeStateRequirement = Literal["best_effort", "required"]


class InstrumentSafeOperationArgument(BaseModel):
    """One serializable argument for a configured device-safe operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: _NonEmptyId
    value: StateValue


class InstrumentSafeOperation(BaseModel):
    """One provider-declared operation executed only during device finalization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    interface_id: InterfaceId
    component_path: tuple[_NonEmptyId, ...] = ()
    operation_id: _NonEmptyId
    arguments: tuple[InstrumentSafeOperationArgument, ...] = ()

    @field_validator("arguments")
    @classmethod
    def validate_unique_arguments(
        cls,
        value: tuple[InstrumentSafeOperationArgument, ...],
    ) -> tuple[InstrumentSafeOperationArgument, ...]:
        argument_ids = [argument.id for argument in value]
        if len(argument_ids) != len(set(argument_ids)):
            raise ValueError("safe operation argument ids must be unique")
        return value


class InstrumentSpec(BaseModel):
    """Configured instrument with a stable physical access domain.

    Default and safe states are sparse patches over freshly observed state.
    After exclusive acquisition, ``run_start`` either preserves that observed
    baseline or applies ``default_state`` to establish the execution baseline.
    A successful run either releases its final authored state, restores that
    baseline, or applies the configured safe-state actions before terminal
    readback. Failure always aborts first and may then apply the same safe-state
    actions while the instrument remains commandable. Safe operations run in
    declaration order before the sparse ``safe_state`` patch. A required safe
    state keeps the physical access domain quarantined when a known rejection
    prevents completion.
    """

    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(min_length=1)]
    exclusivity_key: Annotated[str, Field(min_length=1)]
    driver_id: Annotated[str, Field(min_length=1)]
    connection: InstrumentConnection
    default_state: list[InstrumentStateSetting] = Field(default_factory=list)
    run_start: InstrumentRunStartPolicy
    success_action: InstrumentSuccessAction
    safe_state: list[InstrumentStateSetting] = Field(default_factory=list)
    safe_operations: list[InstrumentSafeOperation] = Field(
        default_factory=list,
        exclude_if=_exclude_empty,
    )
    safe_state_requirement: InstrumentSafeStateRequirement = Field(
        default_factory=_default_best_effort,
        exclude_if=_exclude_best_effort,
        description="Defaults to best_effort when omitted.",
    )
    failure_action: InstrumentFailureAction

    @field_validator("default_state", "safe_state")
    @classmethod
    def validate_unique_state_targets(
        cls,
        value: list[InstrumentStateSetting],
    ) -> list[InstrumentStateSetting]:
        identities = [state_member_identity(item.target) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("configured state property targets must be unique")
        return value

    @model_validator(mode="after")
    def validate_lifecycle_state(self) -> InstrumentSpec:
        if self.run_start == "apply_default_state" and not self.default_state:
            raise ValueError("apply_default_state requires a non-empty default state")
        uses_safe_state = (
            self.success_action == "apply_safe_state"
            or self.failure_action == "abort_then_safe_state"
        )
        has_safe_actions = bool(self.safe_state or self.safe_operations)
        if uses_safe_state and not has_safe_actions:
            raise ValueError("safe-state finalization requires a non-empty action")
        if self.safe_state_requirement == "required" and not uses_safe_state:
            raise ValueError(
                "required safe state must be selected by a finalization action"
            )
        return self


class InstrumentRegistry(BaseModel):
    """Logical instruments with one owner for each physical access domain."""

    model_config = ConfigDict(extra="forbid")

    instruments: list[InstrumentSpec]

    @field_validator("instruments")
    @classmethod
    def validate_instruments(cls, value: list[InstrumentSpec]) -> list[InstrumentSpec]:
        instruments = _ensure_unique(value, "instrument")
        exclusivity_keys = [instrument.exclusivity_key for instrument in instruments]
        if len(exclusivity_keys) != len(set(exclusivity_keys)):
            raise ValueError("instrument exclusivity keys must be unique")
        return instruments


class ResourceRoleSpec(BaseModel):
    """One documented purpose that authors may select explicitly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    description: str | None = Field(default=None, min_length=1)


class RoutingEndpoint(BaseModel):
    """One logical binding onto a physical interface component.

    ``entity_id`` narrows the endpoint to one entity served by its route; an
    omitted entity applies it to every route entity, or to entityless work when
    the route has none. ``channel_id`` is the logical interface channel and
    ``component_path`` identifies the owning physical subcomponent exposed by
    the instrument driver.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    interface_id: InterfaceId
    entity_id: str | None = None
    channel_id: str | None = None
    component_path: tuple[
        Annotated[str, Field(min_length=1)],
        ...,
    ] = ()


class ResourceRoute(BaseModel):
    """A selectable physical resource and all endpoints it owns together.

    The route binds one instrument, optional lab-purpose role, finite set of
    served entities, and the interface endpoints selected as one resource.
    Endpoint component paths preserve shared physical ownership when several
    logical channels meet at the same device property.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    role_id: str | None = Field(default=None, min_length=1)
    entity_ids: list[_NonEmptyId] = Field(default_factory=list)
    endpoints: list[RoutingEndpoint] = Field(min_length=1)

    @field_validator("entity_ids")
    @classmethod
    def validate_entity_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("resource route entity ids must be unique")
        return value

    @field_validator("endpoints")
    @classmethod
    def validate_endpoints(cls, value: list[RoutingEndpoint]) -> list[RoutingEndpoint]:
        seen: set[tuple[str, str | None, str | None, tuple[str, ...]]] = set()
        for endpoint in value:
            identity = (
                endpoint.interface_id,
                endpoint.entity_id,
                endpoint.channel_id,
                endpoint.component_path,
            )
            if identity in seen:
                msg = (
                    "duplicate resource route endpoint: "
                    f"interface={endpoint.interface_id}, entity={endpoint.entity_id}, "
                    f"channel={endpoint.channel_id}, "
                    f"component_path={endpoint.component_path}"
                )
                raise ValueError(msg)
            seen.add(identity)
        return value

    @model_validator(mode="after")
    def validate_endpoint_entities_are_served(self) -> ResourceRoute:
        served = set(self.entity_ids)
        unserved = sorted(
            {
                endpoint.entity_id
                for endpoint in self.endpoints
                if endpoint.entity_id is not None and endpoint.entity_id not in served
            }
        )
        if unserved:
            raise ValueError(
                f"resource route {self.id!r} endpoints reference entities not "
                f"served by the route: {', '.join(unserved)}"
            )
        return self


class RoutingGraph(BaseModel):
    """Finite static resource-route catalog in an accepted system snapshot.

    Planning may project logical interface and entity selections through this
    catalog, but it never uses it for live availability, load balancing, or
    implicit failover.
    """

    model_config = ConfigDict(extra="forbid")

    roles: list[ResourceRoleSpec] = Field(default_factory=list)
    routes: list[ResourceRoute] = Field(default_factory=list)

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, value: list[ResourceRoleSpec]) -> list[ResourceRoleSpec]:
        return _ensure_unique(value, "resource role")

    @field_validator("routes")
    @classmethod
    def validate_routes(cls, value: list[ResourceRoute]) -> list[ResourceRoute]:
        return _ensure_unique(value, "resource route")

    @model_validator(mode="after")
    def validate_role_references_and_ownership(self) -> RoutingGraph:
        role_ids = {role.id for role in self.roles}
        ownership: dict[tuple[str | None, str, str | None], str] = {}
        for route in self.routes:
            if route.role_id is not None and route.role_id not in role_ids:
                msg = (
                    f"resource route {route.id!r} references unknown role "
                    f"{route.role_id!r}"
                )
                raise ValueError(msg)
            for endpoint in route.endpoints:
                served_entities: tuple[str | None, ...]
                if endpoint.entity_id is not None:
                    served_entities = (endpoint.entity_id,)
                elif route.entity_ids:
                    served_entities = tuple(route.entity_ids)
                else:
                    served_entities = (None,)
                for entity_id in served_entities:
                    identity = (route.role_id, endpoint.interface_id, entity_id)
                    owner = ownership.get(identity)
                    if owner is not None and owner != route.id:
                        msg = (
                            "resource endpoint has multiple routes for the same role: "
                            f"routes={owner!r}, {route.id!r}, "
                            f"role={route.role_id!r}, "
                            f"interface={endpoint.interface_id}, "
                            f"entity={entity_id!r}"
                        )
                        raise ValueError(msg)
                    ownership[identity] = route.id
        return self


class DomainTargetBinding(BaseModel):
    """One composite target and the instruments it is authorized to coordinate."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    instrument_ids: list[str] = Field(default_factory=list)

    @field_validator("instrument_ids")
    @classmethod
    def validate_instrument_ids(
        cls,
        value: list[str],
    ) -> list[str]:
        if any(not instrument_id for instrument_id in value):
            raise ValueError("domain target instrument ids must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("domain target instrument ids must be unique")
        return value


class SystemSpec(BaseModel):
    """Stable system topology and logical parameter definitions."""

    model_config = ConfigDict(extra="forbid")

    id: str
    topology: Topology
    instrument_registry: InstrumentRegistry
    scenario: SoftwareExecutionScenario | None = None
    routing: RoutingGraph = Field(default_factory=RoutingGraph)
    domain_target: DomainTargetBinding | None
    parameter_catalog: ParameterCatalog

    @model_validator(mode="after")
    def validate_domain_target_members(self) -> SystemSpec:
        if self.scenario is not None:
            for instrument in self.instrument_registry.instruments:
                if not isinstance(instrument.connection, VirtualInstrumentConnection):
                    raise ValueError(
                        "software scenario requires virtual connection: "
                        f"{instrument.id}"
                    )
        target = self.domain_target
        if target is None:
            return self
        known_instrument_ids = {
            instrument.id for instrument in self.instrument_registry.instruments
        }
        for instrument_id in target.instrument_ids:
            if instrument_id not in known_instrument_ids:
                raise ValueError(f"unknown domain target instrument: {instrument_id}")
        return self


class ConfigProfileSnapshot(BaseModel):
    """Immutable config profile snapshot used by runs and ConfigRegistry entries."""

    model_config = ConfigDict(extra="forbid")

    id: str
    system: SystemSpec
    parameter_snapshot: ParameterSnapshot

    @property
    def topology(self) -> Topology:
        return self.system.topology

    @property
    def instrument_registry(self) -> InstrumentRegistry:
        return self.system.instrument_registry

    @property
    def routing(self) -> RoutingGraph:
        return self.system.routing

    @property
    def domain_target(self) -> DomainTargetBinding | None:
        return self.system.domain_target

    @property
    def parameter_catalog(self) -> ParameterCatalog:
        return self.system.parameter_catalog


def instrument_bindings(
    config: ConfigProfileSnapshot,
) -> tuple[InstrumentBindingSpec, ...]:
    """Project configured instruments onto the provider-visible binding ABI."""

    return tuple(
        InstrumentBindingSpec(
            id=instrument.id,
            driver_id=instrument.driver_id,
            connection=instrument.connection.model_copy(deep=True),
        )
        for instrument in config.instrument_registry.instruments
    )


def snapshot_config_profile(
    *,
    profile_id: str,
    system: SystemSpec,
    parameter_snapshot: ParameterSnapshot,
) -> ConfigProfileSnapshot:
    """Build one immutable runtime configuration snapshot."""

    return ConfigProfileSnapshot(
        id=profile_id,
        system=system,
        parameter_snapshot=parameter_snapshot,
    )


def config_content_equal(
    left: ConfigProfileSnapshot,
    right: ConfigProfileSnapshot,
) -> bool:
    """Compare complete runtime configuration values."""

    return left == right


def config_content_hash(config: ConfigProfileSnapshot) -> ConfigContentHash:
    """Return a stable content address for a complete config snapshot."""

    content = json.dumps(
        config.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
