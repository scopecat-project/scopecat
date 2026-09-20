"""Resolve logical operations to point-effective pulse implementations."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash

from scopecat_quantum._ids import (
    CircuitOperationId,
    CouplerId,
    GateId,
    PulseImplementationId,
    QubitId,
)
from scopecat_quantum.circuits import Measure, VerifiedCircuitOperations
from scopecat_quantum.gates import (
    GateArgumentValue,
    GateCall,
    canonical_gate_quantity,
)
from scopecat_quantum.measurement_implementations import (
    MeasurementPulseImplementation,
    MeasurementPulseImplementationBinding,
    MeasurementPulseImplementationKey,
)
from scopecat_quantum.pulses import (
    PulseProgram,
    pulse_leaf_owners,
)


@dataclass(frozen=True, slots=True)
class GatePulseImplementationArgument:
    """A snapshotted, named argument in an exact implementation key."""

    id: str
    value: GateArgumentValue

    def __post_init__(self) -> None:
        if not self.id.strip():
            msg = "implementation argument id must be a non-empty string"
            raise ValueError(msg)
        value = self.value
        if isinstance(value, bool):
            msg = "implementation argument value must be finite"
            raise ValueError(msg)
        if isinstance(value, int):
            return
        if isinstance(value, float):
            if math.isfinite(value):
                return
        else:
            if math.isfinite(value.value):
                try:
                    canonical = canonical_gate_quantity(value)
                except ValueError:
                    pass
                else:
                    object.__setattr__(self, "value", canonical)
                    return
        msg = "implementation argument value must be finite"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class GatePulseImplementationKey:
    """Exact data-only identity of a logical gate implementation."""

    gate_id: GateId
    operands: tuple[QubitId, ...]
    arguments: tuple[GatePulseImplementationArgument, ...] = ()

    def __post_init__(self) -> None:
        if not self.operands:
            msg = "gate implementation keys require at least one operand"
            raise ValueError(msg)
        if len(set(self.operands)) != len(self.operands):
            msg = "gate implementation key operands must be unique"
            raise ValueError(msg)
        arguments = self.arguments
        argument_ids = tuple(argument.id for argument in arguments)
        if len(set(argument_ids)) != len(argument_ids):
            msg = "gate implementation key argument ids must be unique"
            raise ValueError(msg)
        object.__setattr__(
            self,
            "arguments",
            tuple(sorted(arguments, key=lambda argument: argument.id)),
        )

    @classmethod
    def from_call(cls, call: GateCall) -> GatePulseImplementationKey:
        return cls(
            gate_id=call.gate_id,
            operands=call.qubits,
            arguments=tuple(
                GatePulseImplementationArgument(id=argument.id, value=argument.value)
                for argument in call.arguments
            ),
        )


@dataclass(frozen=True, slots=True)
class GatePulseImplementation:
    """One resolved logical gate implementation with a reusable pulse template.

    The template contains template-relative event identities. Gate-to-pulse
    lowering hygienically prefixes those identities for every call; it never
    concatenates this object as an already-instantiated program.
    """

    id: PulseImplementationId
    key: GatePulseImplementationKey
    pulse_template: PulseProgram
    resources: tuple[CouplerId, ...] = ()
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        resources = tuple(self.resources)
        if len(set(resources)) != len(resources):
            msg = "gate implementation resources must be unique"
            raise ValueError(msg)
        if self.pulse_template.acquisition_slots:
            msg = "gate implementation pulse templates cannot declare acquisition slots"
            raise ValueError(msg)
        owners = set(pulse_leaf_owners(self.pulse_template.body))
        allowed_owners = {*self.key.operands, *resources}
        foreign_owners = owners - allowed_owners
        if foreign_owners:
            rendered = ", ".join(
                repr(owner.value)
                for owner in sorted(foreign_owners, key=lambda item: item.value)
            )
            msg = f"gate implementation contains unauthorized signal owners: {rendered}"
            raise ValueError(msg)
        used_resources = {owner for owner in owners if isinstance(owner, CouplerId)}
        unused_resources = set(resources) - used_resources
        if unused_resources:
            rendered = ", ".join(
                repr(owner.value)
                for owner in sorted(unused_resources, key=lambda item: item.value)
            )
            msg = f"gate implementation declares unused coupler resources: {rendered}"
            raise ValueError(msg)
        object.__setattr__(self, "resources", resources)
        object.__setattr__(
            self,
            "fingerprint",
            stable_content_hash(
                content_fingerprint(
                    {
                        "schema": "scopecat_quantum.gate_pulse_implementation.v1",
                        "id": self.id,
                        "key": self.key,
                        "pulse_template": self.pulse_template,
                        "resources": resources,
                    }
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class GatePulseImplementationBinding:
    """Exact resolved pulse template for one gate invocation.

    ``pulse_template`` still carries implementation-local identities. The lowerer
    consuming this binding must instantiate it into a fresh hygienic scope.
    """

    call_id: CircuitOperationId
    key: GatePulseImplementationKey
    implementation_id: PulseImplementationId
    implementation_fingerprint: str
    pulse_template: PulseProgram


@dataclass(frozen=True, slots=True)
class ResolvedPulseImplementations:
    """Point-effective gate and measurement implementations, unique by key."""

    gates: tuple[GatePulseImplementation, ...] = ()
    measurements: tuple[MeasurementPulseImplementation, ...] = ()

    def __post_init__(self) -> None:
        gates = tuple(self.gates)
        measurements = tuple(self.measurements)
        implementation_ids = tuple(entry.id for entry in (*gates, *measurements))
        if len(set(implementation_ids)) != len(implementation_ids):
            msg = "pulse implementation ids must be unique"
            raise ValueError(msg)
        gate_keys = tuple(entry.key for entry in gates)
        if len(set(gate_keys)) != len(gate_keys):
            msg = "gate pulse implementation keys must be unique"
            raise ValueError(msg)
        measurement_keys = tuple(entry.key for entry in measurements)
        if len(set(measurement_keys)) != len(measurement_keys):
            msg = "measurement pulse implementation keys must be unique"
            raise ValueError(msg)
        object.__setattr__(
            self, "gates", tuple(sorted(gates, key=lambda item: item.id.value))
        )
        object.__setattr__(
            self,
            "measurements",
            tuple(sorted(measurements, key=lambda item: item.id.value)),
        )


type PulseImplementationKey = (
    GatePulseImplementationKey | MeasurementPulseImplementationKey
)
type PulseImplementationBinding = (
    GatePulseImplementationBinding | MeasurementPulseImplementationBinding
)


def _binding_operation_id(binding: PulseImplementationBinding) -> CircuitOperationId:
    if isinstance(binding, GatePulseImplementationBinding):
        return binding.call_id
    return binding.measurement_id


class PulseImplementationBindingIssueCode(StrEnum):
    """Stable kinds of pulse implementation binding failure."""

    MISSING = "pulse_implementation_missing"


@dataclass(frozen=True, slots=True)
class PulseImplementationBindingIssue:
    """One operation without a resolved pulse implementation."""

    code: PulseImplementationBindingIssueCode
    operation_id: CircuitOperationId
    key: PulseImplementationKey
    message: str

    def __post_init__(self) -> None:
        if not self.message.strip():
            msg = "pulse implementation binding issue message must be non-empty"
            raise ValueError(msg)


class PulseImplementationBindingError(ValueError):
    """Aggregate exact implementation coverage failure."""

    def __init__(self, issues: tuple[PulseImplementationBindingIssue, ...]) -> None:
        if not issues:
            msg = "pulse implementation binding errors require at least one issue"
            raise ValueError(msg)
        self.issues = tuple(
            sorted(
                issues,
                key=lambda issue: (
                    issue.operation_id.value,
                    issue.code.value,
                ),
            )
        )
        super().__init__("; ".join(issue.message for issue in self.issues))


@dataclass(frozen=True, slots=True)
class PulseImplementationBindings:
    """Resolved pulse implementations for every operation in one circuit."""

    bindings: tuple[PulseImplementationBinding, ...]

    def binding_for(
        self, operation_id: CircuitOperationId
    ) -> PulseImplementationBinding:
        for binding in self.bindings:
            if _binding_operation_id(binding) == operation_id:
                return binding
        msg = f"operation {operation_id.value!r} has no pulse implementation binding"
        raise KeyError(msg)


@dataclass(frozen=True, slots=True)
class PulseImplementationIndex:
    """Exact implementation lookup reusable during retained-tree expansion."""

    gates_by_key: dict[GatePulseImplementationKey, GatePulseImplementation]
    measurements_by_key: dict[
        MeasurementPulseImplementationKey,
        MeasurementPulseImplementation,
    ]

    @classmethod
    def from_implementations(
        cls,
        implementations: ResolvedPulseImplementations,
    ) -> PulseImplementationIndex:
        return cls(
            gates_by_key={entry.key: entry for entry in implementations.gates},
            measurements_by_key={
                entry.key: entry for entry in implementations.measurements
            },
        )

    def binding_for(
        self,
        operation: GateCall | Measure,
    ) -> PulseImplementationBinding:
        """Bind one concrete operation without retaining a full expanded circuit."""

        if isinstance(operation, GateCall):
            gate_key = GatePulseImplementationKey.from_call(operation)
            gate = self.gates_by_key.get(gate_key)
            if gate is not None:
                return GatePulseImplementationBinding(
                    call_id=operation.id,
                    key=gate_key,
                    implementation_id=gate.id,
                    implementation_fingerprint=gate.fingerprint,
                    pulse_template=gate.pulse_template,
                )
            key: PulseImplementationKey = gate_key
        else:
            measurement_key = MeasurementPulseImplementationKey.from_measurement(
                operation
            )
            measurement = self.measurements_by_key.get(measurement_key)
            if measurement is not None:
                return MeasurementPulseImplementationBinding(
                    measurement_id=operation.id,
                    key=measurement_key,
                    implementation_id=measurement.id,
                    implementation_fingerprint=measurement.fingerprint,
                    pulse_template=measurement.pulse_template,
                )
            key = measurement_key
        raise PulseImplementationBindingError(
            (
                PulseImplementationBindingIssue(
                    code=PulseImplementationBindingIssueCode.MISSING,
                    operation_id=operation.id,
                    key=key,
                    message=(
                        f"operation {operation.id.value!r} has no exact "
                        "pulse implementation"
                    ),
                ),
            )
        )


def bind_pulse_implementations(
    operations: VerifiedCircuitOperations,
    implementations: ResolvedPulseImplementations,
) -> PulseImplementationBindings:
    """Bind every logical operation to its exact resolved pulse implementation."""

    index = PulseImplementationIndex.from_implementations(implementations)
    bindings: list[PulseImplementationBinding] = []
    issues: list[PulseImplementationBindingIssue] = []
    for operation in operations.operations:
        try:
            bindings.append(index.binding_for(operation))
        except PulseImplementationBindingError as error:
            issues.extend(error.issues)

    if issues:
        raise PulseImplementationBindingError(tuple(issues))

    return PulseImplementationBindings(tuple(bindings))


__all__ = [
    "GatePulseImplementation",
    "GatePulseImplementationArgument",
    "GatePulseImplementationBinding",
    "GatePulseImplementationKey",
    "MeasurementPulseImplementation",
    "MeasurementPulseImplementationBinding",
    "MeasurementPulseImplementationKey",
    "PulseImplementationBinding",
    "PulseImplementationBindingError",
    "PulseImplementationBindingIssue",
    "PulseImplementationBindingIssueCode",
    "PulseImplementationBindings",
    "PulseImplementationIndex",
    "PulseImplementationKey",
    "ResolvedPulseImplementations",
    "bind_pulse_implementations",
]
