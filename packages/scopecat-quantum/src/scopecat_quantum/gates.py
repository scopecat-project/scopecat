"""Hardware-independent gate definitions and invocations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from scopecat import Quantity
from scopecat.kernel.units import UNIT_KINDS, UNIT_SCALE_TO_BASE
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_types import Scalar
from scopecat.kernel.value_validation import ValueValidationError, coerce_literal

from scopecat_quantum._ids import CircuitOperationId, GateId, QubitId


class GateParameterKind(StrEnum):
    """Scalar parameter kinds understood by the foundational gate IR."""

    NUMBER = "number"
    INTEGER = "integer"
    ANGLE = "angle"


@dataclass(frozen=True, slots=True)
class GateParameterDefinition:
    """One named parameter in a gate definition."""

    id: str
    kind: GateParameterKind | QuantityType

    def __post_init__(self) -> None:
        if not self.id.strip():
            msg = "gate parameter id must be a non-empty string"
            raise ValueError(msg)


def gate_parameter_label(kind: GateParameterKind | QuantityType) -> str:
    return kind.value if isinstance(kind, GateParameterKind) else repr(kind)


def quantity_argument_matches(value: object, kind: QuantityType) -> bool:
    if not isinstance(value, Quantity):
        return False
    try:
        coerce_literal(Scalar(kind), value, path=())
    except ValueValidationError:
        return False
    return True


def canonical_gate_quantity(value: Quantity) -> Quantity:
    """Normalize linear units before recipe and exact implementation identity."""
    if value.unit not in UNIT_SCALE_TO_BASE:
        return value
    base = next(
        unit
        for unit, scale in UNIT_SCALE_TO_BASE.items()
        if scale == 1 and UNIT_KINDS[unit] == UNIT_KINDS[value.unit]
    )
    return value.to(base)


@dataclass(frozen=True, slots=True)
class GateDefinition:
    """Reusable gate semantics, separate from any circuit invocation."""

    id: GateId
    qubit_arity: int
    parameters: tuple[GateParameterDefinition, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.qubit_arity, bool) or self.qubit_arity <= 0:
            msg = "gate qubit_arity must be a positive integer"
            raise ValueError(msg)


type GateArgumentValue = int | float | Quantity


def canonical_angle_value(value: Quantity) -> Quantity:
    """Return the stable radian representation used by exact match keys.

    The core quantity conversion owns the shared rounding rule, so
    ``180 deg`` and ``pi rad`` converge to the same transient value here.
    """

    return value.to("rad")


@dataclass(frozen=True, slots=True)
class GateArgument:
    """One explicitly named gate argument."""

    id: str
    value: GateArgumentValue

    def __post_init__(self) -> None:
        if not self.id.strip():
            msg = "gate argument id must be a non-empty string"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class GateCall:
    """One invocation of a gate definition on logical qubits."""

    id: CircuitOperationId
    gate_id: GateId
    qubits: tuple[QubitId, ...]
    arguments: tuple[GateArgument, ...] = ()
