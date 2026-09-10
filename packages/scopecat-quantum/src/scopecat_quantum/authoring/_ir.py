# pyright: reportUnusedClass=false
"""Symbolic quantum authoring carriers and fragment IR."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, overload, override

from scopecat.authoring import (
    Input as ExperimentInput,
)
from scopecat.program.value_types import (
    Entity as EntityAtomType,
)
from scopecat.program.value_types import Scalar, Table, TableColumn

from scopecat_quantum._ids import (
    AcquisitionSlotId,
    CouplerId,
    PulseProgramId,
    QubitId,
)
from scopecat_quantum.acquisitions import (
    AcquisitionKind,
    QuantumResultContract,
)
from scopecat_quantum.gates import (
    GateArgumentValue,
    GateDefinition,
)
from scopecat_quantum.pulses import (
    AcquireSignal,
    AnalyticEnvelope,
    EnvelopePhaseReference,
    FrameSignal,
    PlaySignal,
)

from ._expressions import ProgramInput as ProgramInput
from ._expressions import QuantityExpression
from ._expressions import QuantumQuantity as QuantumQuantity


@dataclass(frozen=True, slots=True, repr=False)
class Qubit:
    """A logical qubit handle, independent of physical target wiring."""

    ir_id: QubitId

    @property
    def id(self) -> str:
        """Return the logical qubit port identity."""

        return self.ir_id.value


@dataclass(frozen=True, slots=True, repr=False)
class Coupler:
    """A logical coupler handle, independent of physical target wiring."""

    ir_id: CouplerId

    @property
    def id(self) -> str:
        """Return the logical coupler port identity."""

        return self.ir_id.value


@dataclass(frozen=True, slots=True, repr=False)
class QubitSet:
    """A symbolic, variable-size set of logical qubits."""

    _id: str
    _item: Qubit

    @property
    def id(self) -> str:
        """Return the stable set-port identity."""

        return self._id

    @property
    def item(self) -> Qubit:
        """Return the symbolic member used inside retained set operations."""

        return self._item

    @property
    def value_type(self) -> Table:
        """Return the one-column entity table carried through core planning."""

        return Table(
            columns=(
                TableColumn(
                    "qubit",
                    Scalar(EntityAtomType(entity_kind="logical_qubit")),
                ),
            ),
            primary_key=("qubit",),
        )


@dataclass(frozen=True, slots=True, repr=False)
class CouplerSet:
    """A symbolic, variable-size set of logical couplers."""

    _id: str
    _item: Coupler

    @property
    def id(self) -> str:
        return self._id

    @property
    def item(self) -> Coupler:
        return self._item

    @property
    def value_type(self) -> Table:
        return Table(
            columns=(
                TableColumn(
                    "coupler",
                    Scalar(EntityAtomType(entity_kind="logical_coupler")),
                ),
            ),
            primary_key=("coupler",),
        )


@dataclass(frozen=True, slots=True, repr=False)
class QubitPair:
    """One symbolic topology edge with its two endpoints and coupler entity."""

    left: Qubit
    right: Qubit
    coupler: Coupler


@dataclass(frozen=True, slots=True, repr=False)
class QubitPairSet:
    """A symbolic, variable-size set of topology-backed qubit pairs."""

    _id: str
    _item: QubitPair

    @property
    def id(self) -> str:
        return self._id

    @property
    def item(self) -> QubitPair:
        return self._item

    @property
    def value_type(self) -> Table:
        return Table(
            columns=(
                TableColumn(
                    "left",
                    Scalar(EntityAtomType(entity_kind="logical_qubit")),
                ),
                TableColumn(
                    "right",
                    Scalar(EntityAtomType(entity_kind="logical_qubit")),
                ),
                TableColumn(
                    "coupler",
                    Scalar(EntityAtomType(entity_kind="logical_coupler")),
                ),
            ),
            primary_key=("coupler",),
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class MeasurementResult:
    """One typed result produced by logical measurement or pulse acquisition."""

    _id: str
    _qubit: Qubit
    contract: QuantumResultContract
    _entity_set: QubitSet | None = None

    @property
    def id(self) -> str:
        """Return the stable result-port identity."""

        return self._id

    @property
    def qubit(self) -> Qubit:
        """Return the logical qubit measured for this result."""

        return self._qubit

    @property
    def entity_set(self) -> QubitSet | None:
        """Return the retained entity set for an aggregate result, if any."""

        return self._entity_set

    @property
    def acquisition_kind(self) -> AcquisitionKind:
        """Return the physical acquisition kind promised by the contract."""

        return self.contract.acquisition_kind

    @property
    def acquisition_slot_id(self) -> AcquisitionSlotId:
        """Return the acquisition identity used by materialized circuit IR."""

        return AcquisitionSlotId(self._id)


type ProgramResult = MeasurementResult


@dataclass(frozen=True, slots=True, repr=False)
class ProgramResults(Sequence[ProgramResult]):
    """Source-ordered quantum results with stable name lookup."""

    _values: tuple[ProgramResult, ...]

    @overload
    def __getitem__(self, index: int) -> ProgramResult: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[ProgramResult, ...]: ...

    @overload
    def __getitem__(self, index: str) -> ProgramResult: ...

    @override
    def __getitem__(
        self,
        index: int | slice | str,
    ) -> ProgramResult | tuple[ProgramResult, ...]:
        if isinstance(index, str):
            for result in self._values:
                if result.id == index:
                    return result
            raise KeyError(index)
        return self._values[index]

    @override
    def __len__(self) -> int:
        return len(self._values)

    @override
    def __iter__(self) -> Iterator[ProgramResult]:
        return iter(self._values)

    def __getattr__(self, result_id: str) -> ProgramResult:
        try:
            return self[result_id]
        except KeyError:
            msg = f"quantum program has no result {result_id!r}"
            raise AttributeError(msg) from None

    @override
    def __dir__(self) -> list[str]:
        return sorted((*super().__dir__(), *(result.id for result in self._values)))


class QuantumFragment:
    """Opaque base type accepted by unified quantum composition factories."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError(
            "QuantumFragment has no standalone state; construct a concrete fragment"
        )


class CircuitFragment(QuantumFragment):
    """Opaque logical-only fragment that can be closed as a circuit."""

    __slots__ = ()


class PulseFragment(QuantumFragment):
    """Opaque pulse statement that composes beside gates and measurements."""

    __slots__ = ()


type CircuitArgument = GateArgumentValue | ProgramInput | QuantityExpression


type EntitySetPort = QubitSet | CouplerSet | QubitPairSet


type ProgramPort = PulseElement | EntitySetPort | ProgramInput


type ProgramFunction = Callable[..., QuantumFragment]


type FragmentFunction = Callable[..., QuantumFragment]


type PulseTemplateFunction = Callable[..., QuantumFragment]


type ElementBindings = Mapping[QubitId | CouplerId, QubitId | CouplerId]


_SHOTS_INPUT_ID = "__shots__"


_RESERVED_PROGRAM_PORT_IDS = frozenset({_SHOTS_INPUT_ID})


_RESERVED_RESULT_IDS = frozenset({"count", "index"})


type RepeatCount = int | ProgramInput


type PulseElement = Qubit | Coupler


type QubitInput = Annotated[
    ExperimentInput[str], EntityAtomType(entity_kind="logical_qubit")
]


type CouplerInput = Annotated[
    ExperimentInput[str], EntityAtomType(entity_kind="logical_coupler")
]


QUANTUM_PROGRAM_DIALECT_ID = "scopecat.quantum.program"


QUANTUM_PROGRAM_DIALECT_VERSION = "6"


class _GateHandle(Protocol):
    @property
    def definition(self) -> GateDefinition: ...

    @property
    def id(self) -> str: ...


class _PulseTemplateHandle(Protocol):
    @property
    def ir_id(self) -> PulseProgramId: ...

    @property
    def id(self) -> str: ...


class _ProgramFamilyEnvelope(Protocol):
    @property
    def gate_definitions(self) -> tuple[GateDefinition, ...]: ...

    @property
    def max_operations(self) -> int: ...

    @property
    def max_depth(self) -> int: ...


class _FragmentHandle(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def parameters(self) -> tuple[ProgramPort, ...]: ...

    @property
    def envelope(self) -> _ProgramFamilyEnvelope: ...

    @property
    def __wrapped__(self) -> Callable[..., QuantumFragment]: ...


@dataclass(frozen=True, slots=True, repr=False)
class Measurement(CircuitFragment):
    """A measurement statement and its first-class acquisition result."""

    result: MeasurementResult


@dataclass(frozen=True, slots=True, repr=False)
class PulseEnvelope:
    """A symbolic analytic envelope whose quantities bind with the program."""

    kind: str
    duration: QuantumQuantity
    amplitude: QuantumQuantity
    sigma: QuantumQuantity | None
    derivative_beta: QuantumQuantity | None
    phase: QuantumQuantity
    rise_duration: QuantumQuantity | None = None
    fall_duration: QuantumQuantity | None = None
    frequency_offset: QuantumQuantity | None = None
    frequency_reference: EnvelopePhaseReference = "center"


@dataclass(frozen=True, slots=True, repr=False)
class Acquisition(PulseFragment):
    """A physical acquisition statement and its first-class result port."""

    signal: AcquireSignal
    duration: QuantumQuantity
    result: MeasurementResult


@dataclass(frozen=True, slots=True)
class _GateFragment(CircuitFragment):
    gate: _GateHandle
    qubits: tuple[Qubit, ...]
    arguments: tuple[tuple[str, CircuitArgument], ...]


@dataclass(frozen=True, slots=True)
class _SequenceFragment(CircuitFragment):
    operations: tuple[CircuitFragment, ...]


@dataclass(frozen=True, slots=True)
class _ParallelFragment(CircuitFragment):
    branches: tuple[CircuitFragment, ...]


@dataclass(frozen=True, slots=True)
class _RepeatFragment(CircuitFragment):
    operation: CircuitFragment
    count: int | ProgramInput


@dataclass(frozen=True, slots=True)
class _PlayFragment(PulseFragment):
    signal: PlaySignal
    envelope: PulseEnvelope | AnalyticEnvelope


@dataclass(frozen=True, slots=True)
class _DelayFragment(PulseFragment):
    signal: PlaySignal
    duration: QuantumQuantity


@dataclass(frozen=True, slots=True)
class _ShiftPhaseFragment(PulseFragment):
    signal: FrameSignal
    phase: QuantumQuantity


@dataclass(frozen=True, slots=True)
class _PulseTemplateCallFragment(PulseFragment):
    template: _PulseTemplateHandle
    body: QuantumFragment


@dataclass(frozen=True, slots=True)
class _QuantumSequenceFragment(QuantumFragment):
    operations: tuple[QuantumFragment, ...]


@dataclass(frozen=True, slots=True)
class _QuantumParallelFragment(QuantumFragment):
    branches: tuple[QuantumFragment, ...]
    alignment: Literal["start", "end"] = "start"


@dataclass(frozen=True, slots=True)
class _ParallelEachFragment(QuantumFragment):
    entity_set: QubitSet
    operation: QuantumFragment


@dataclass(frozen=True, slots=True)
class _ParallelCouplerEachFragment(QuantumFragment):
    entity_set: CouplerSet
    operation: QuantumFragment


@dataclass(frozen=True, slots=True)
class _ParallelQubitPairEachFragment(QuantumFragment):
    entity_set: QubitPairSet
    operation: QuantumFragment


@dataclass(frozen=True, slots=True)
class _QuantumRepeatFragment(QuantumFragment):
    operation: QuantumFragment
    count: RepeatCount
    result_dimension_id: str | None = None


@dataclass(frozen=True, slots=True)
class _ConditionalFragment(QuantumFragment):
    predicate: MeasurementResult
    cases: tuple[tuple[int, QuantumFragment], ...]
    default: QuantumFragment | None = None


@dataclass(frozen=True, slots=True)
class _ImplementedGateFragment(QuantumFragment):
    gate: _GateFragment
    pulse: QuantumFragment
    candidate_id: str | None


@dataclass(frozen=True, slots=True)
class _FragmentCall(QuantumFragment):
    definition: _FragmentHandle
    arguments: tuple[tuple[str, object], ...]


@dataclass(frozen=True, slots=True)
class _ExpandedFragment(QuantumFragment):
    definition_id: str
    body: QuantumFragment


class ProgramBindingError(ValueError):
    """Raised when concrete bindings cannot close a symbolic program."""
