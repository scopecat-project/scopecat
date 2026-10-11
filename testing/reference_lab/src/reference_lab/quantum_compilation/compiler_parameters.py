"""Typed point-effective parameter values consumed by the quantum compiler."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from scopecat import Quantity
from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash
from scopecat.kernel.entity import EntityRef
from scopecat_quantum._ids import QubitId


@dataclass(frozen=True, slots=True)
class QubitPulseParameters:
    """Resolved DRAG pulse values for one logical qubit."""

    qubit: QubitId
    quarter_turn_duration: Quantity
    quarter_turn_amplitude: Quantity
    quarter_turn_sigma: Quantity
    drag_beta: Quantity

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> QubitPulseParameters:
        qubit = cast("EntityRef", row["qubit"])
        return cls(
            qubit=QubitId(qubit.id),
            quarter_turn_duration=cast("Quantity", row["quarter_turn_duration"]),
            quarter_turn_amplitude=cast("Quantity", row["quarter_turn_amplitude"]),
            quarter_turn_sigma=cast("Quantity", row["quarter_turn_sigma"]),
            drag_beta=cast("Quantity", row["drag_beta"]),
        )


@dataclass(frozen=True, slots=True)
class QuantumCompilerParameters:
    """One immutable snapshot after accepted values and point overlays merge."""

    qubits: tuple[QubitPulseParameters, ...] = ()

    def __post_init__(self) -> None:
        qubits = tuple(self.qubits)
        qubit_ids = tuple(item.qubit for item in qubits)
        if len(set(qubit_ids)) != len(qubit_ids):
            raise ValueError("compiler qubit parameter ids must be unique")
        object.__setattr__(
            self,
            "qubits",
            tuple(sorted(qubits, key=lambda item: item.qubit.value)),
        )

    @classmethod
    def from_qubit_rows(
        cls,
        rows: Sequence[Mapping[str, object]],
    ) -> QuantumCompilerParameters:
        return cls(tuple(QubitPulseParameters.from_row(row) for row in rows))

    @property
    def fingerprint(self) -> str:
        """Identify the exact point-effective values used for lowering."""

        return stable_content_hash(content_fingerprint(self))


__all__ = ["QuantumCompilerParameters", "QubitPulseParameters"]
