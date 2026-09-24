"""Resolved inputs and point coverage for one domain compilation batch."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import cast

from scopecat.inspection import CompiledProgramInspectionQuery
from scopecat.measurements.values import MeasurementValueCatalog
from scopecat.records.parameter import ParameterSnapshot
from scopecat.records.parameter_read import (
    BindingParameterRead,
    DomainInputParameterRead,
)
from scopecat.sdk.domain.view import (
    DomainCallView,
    DomainPointRef,
    DomainProductUseRef,
)


@dataclass(frozen=True, slots=True)
class DomainBatchInputs:
    """Resolved program and compiler input columns for one bounded batch."""

    program: tuple[tuple[str, tuple[object, ...]], ...]
    compiler: tuple[tuple[str, tuple[object, ...]], ...]
    parameter_reads: tuple[DomainInputParameterRead, ...] | None = None
    binding_parameter_reads: tuple[BindingParameterRead, ...] = ()

    def program_input(self, name: str) -> tuple[object, ...]:
        """Return one program input column in request point order."""

        return _input_column(self.program, name)

    def compiler_input(self, name: str) -> tuple[object, ...]:
        """Return one compiler input column in request point order."""

        return _input_column(self.compiler, name)

    def decode_compiler_collection[ItemT, CollectionT](
        self,
        name: str,
        decode: Callable[[Sequence[ItemT]], CollectionT],
    ) -> tuple[CollectionT, ...]:
        """Decode a collection-valued compiler input at every point."""

        return tuple(
            decode(cast("Sequence[ItemT]", value))
            for value in self.compiler_input(name)
        )


@dataclass(frozen=True, slots=True)
class DomainBatchRequest:
    """One complete bounded point batch ready for domain compilation.

    ``parameters`` aligns with ``points`` and includes each point's overlays.
    ``base_parameters`` retains the run's frozen selection. Effective snapshots
    keep its source id; compilers must cache by content, not snapshot id alone.
    """

    batch_ordinal: int
    call: DomainCallView
    inputs: DomainBatchInputs
    points: tuple[DomainPointRef, ...]
    legal_cut_offsets: tuple[int, ...]
    measurement_catalog: MeasurementValueCatalog = field(repr=False)
    base_parameters: ParameterSnapshot = field(repr=False)
    parameters: tuple[ParameterSnapshot, ...] = field(repr=False)
    inspection_requested: bool = False
    inspection_query: CompiledProgramInspectionQuery | None = None

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("domain batch request must contain points")
        if len(self.parameters) != len(self.points):
            raise ValueError("domain parameters must align with request points")
        if (
            not self.legal_cut_offsets
            or self.legal_cut_offsets[-1] != len(self.points)
            or any(
                left >= right
                for left, right in zip(
                    (0, *self.legal_cut_offsets[:-1]),
                    self.legal_cut_offsets,
                    strict=True,
                )
            )
        ):
            raise ValueError(
                "domain batch legal cuts must increase through the complete request"
            )

    @property
    def point_ordinals(self) -> tuple[int, ...]:
        return tuple(point.ordinal for point in self.points)

    @property
    def product_uses(self) -> tuple[DomainProductUseRef, ...]:
        return self.call.product_uses


def _input_column(
    columns: tuple[tuple[str, tuple[object, ...]], ...],
    name: str,
) -> tuple[object, ...]:
    for input_name, values in columns:
        if input_name == name:
            return values
    raise KeyError(name)


__all__ = [
    "DomainBatchInputs",
    "DomainBatchRequest",
]
