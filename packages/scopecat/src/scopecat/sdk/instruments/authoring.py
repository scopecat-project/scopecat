"""Domain values exchanged with an instrument implementation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import JsonValue

from scopecat.kernel.quantity import Quantity
from scopecat.records.costs import OperationCostMeasurement
from scopecat.records.instrument import CommandChannelBinding, ObservationSource
from scopecat.records.measurement import MeasurementAcquisitionValue
from scopecat.sdk.instruments.members import (
    AcquisitionRef,
    AcquisitionResultRef,
    OperationRef,
    StateMemberRef,
)
from scopecat.sdk.problems import Problem

type DriverScalar = bool | int | float | str | Quantity


@dataclass(frozen=True, slots=True)
class DriverStateAssignment:
    """One requested physical member value.

    Entity ids and channel bindings preserve demand and route provenance. They
    do not select a second state slot or replace physical component dispatch.
    """

    target: StateMemberRef
    value: DriverScalar
    entity_ids: tuple[str, ...] = ()
    channel_bindings: tuple[CommandChannelBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class DriverStateObservation:
    """One independently queried or otherwise confirmed member value."""

    target: StateMemberRef
    value: DriverScalar
    source: ObservationSource = "hardware_query"
    coherence_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    channel_bindings: tuple[CommandChannelBinding, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DriverStateReadRequest:
    targets: frozenset[StateMemberRef]


@dataclass(frozen=True, slots=True)
class DriverStateReadback:
    observations: tuple[DriverStateObservation, ...]

    @property
    def values(self) -> dict[StateMemberRef, DriverScalar]:
        return {
            observation.target: observation.value for observation in self.observations
        }

    def with_observation_metadata(
        self,
        metadata: Mapping[str, JsonValue],
        /,
    ) -> DriverStateReadback:
        """Merge common evidence into each member observation."""

        return DriverStateReadback(
            observations=tuple(
                DriverStateObservation(
                    target=observation.target,
                    value=observation.value,
                    source=observation.source,
                    coherence_id=observation.coherence_id,
                    entity_ids=observation.entity_ids,
                    channel_bindings=observation.channel_bindings,
                    metadata={**observation.metadata, **metadata},
                )
                for observation in self.observations
            )
        )


def state_readback[MemberT: StateMemberRef](
    request: DriverStateReadRequest,
    values: Mapping[MemberT, DriverScalar],
    /,
    *,
    evidence: dict[str, JsonValue] | None = None,
    source: ObservationSource = "hardware_query",
    coherence_id: str | None = None,
) -> DriverStateReadback:
    """Build exact member observations with optional common evidence metadata."""

    return DriverStateReadback(
        observations=tuple(
            DriverStateObservation(
                target=target,
                value=value,
                source=source,
                coherence_id=coherence_id,
                metadata={} if evidence is None else dict(evidence),
            )
            for target, value in values.items()
            if target in request.targets
        ),
    )


@dataclass(frozen=True, slots=True)
class DriverStatePatch:
    values: Mapping[StateMemberRef, DriverScalar] = field(
        default_factory=lambda: dict[StateMemberRef, DriverScalar]()
    )
    scoped_values: tuple[DriverStateAssignment, ...] = ()

    @property
    def entries(self) -> tuple[DriverStateAssignment, ...]:
        return (
            *(
                DriverStateAssignment(target=target, value=value)
                for target, value in self.values.items()
            ),
            *self.scoped_values,
        )


@dataclass(frozen=True, slots=True)
class DriverPayload:
    """One opaque operation payload decoded before implementation dispatch."""

    schema_id: str
    value: object = field(repr=False)


type DriverArgument = DriverScalar | DriverPayload


@dataclass(frozen=True, slots=True)
class DriverOperation:
    """One operation dispatched to the physical path in ``target``.

    Entity ids and channel bindings are provenance for the resolved logical
    resource; implementations must not use their cardinality as target identity.
    """

    target: OperationRef
    arguments: Mapping[str, DriverArgument] = field(
        default_factory=lambda: dict[str, DriverArgument]()
    )
    entity_ids: tuple[str, ...] = ()
    channel_bindings: tuple[CommandChannelBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class DriverAcquisitionDimension:
    """One positional axis selection requested from an acquisition result."""

    id: str
    kind: str
    offset: int | None
    size: int | None
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class DriverAcquisition:
    """One acquisition dispatched to the physical path in ``target``.

    Channel bindings describe the route that produced this request. The target
    path remains the driver's authoritative physical dispatch key.
    """

    target: AcquisitionRef
    results: frozenset[AcquisitionResultRef]
    dimensions: Mapping[
        AcquisitionResultRef,
        tuple[DriverAcquisitionDimension, ...],
    ] = field(
        default_factory=lambda: dict[
            AcquisitionResultRef,
            tuple[DriverAcquisitionDimension, ...],
        ]()
    )
    entity_ids: tuple[str, ...] = ()
    channel_bindings: tuple[CommandChannelBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class DriverAcquisitionPlan:
    """Ordered acquisition demand visible before a hardware batch starts."""

    acquisitions: tuple[DriverAcquisition, ...]

    def __post_init__(self) -> None:
        if not self.acquisitions:
            raise ValueError("driver acquisition plans must not be empty")


@dataclass(frozen=True, slots=True)
class DriverReadback:
    values: Mapping[AcquisitionResultRef, MeasurementAcquisitionValue]
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DriverSuccess[T]:
    value: T
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    measured_cost: OperationCostMeasurement | None = None


@dataclass(frozen=True, slots=True)
class DriverRejected:
    problems: tuple[Problem, ...]
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    measured_cost: OperationCostMeasurement | None = None


@dataclass(frozen=True, slots=True)
class DriverUnknown:
    problems: tuple[Problem, ...]
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    measured_cost: OperationCostMeasurement | None = None


type DriverOutcome[T] = DriverSuccess[T] | DriverRejected | DriverUnknown


__all__ = [
    "DriverAcquisition",
    "DriverAcquisitionDimension",
    "DriverAcquisitionPlan",
    "DriverArgument",
    "DriverOperation",
    "DriverOutcome",
    "DriverPayload",
    "DriverReadback",
    "DriverRejected",
    "DriverScalar",
    "DriverStateAssignment",
    "DriverStateObservation",
    "DriverStatePatch",
    "DriverStateReadRequest",
    "DriverStateReadback",
    "DriverSuccess",
    "DriverUnknown",
    "state_readback",
]
