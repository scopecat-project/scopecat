"""Scientific metadata shared by native compute and retained-result annotations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from scopecat.kernel.quantity import Quantity
from scopecat.kernel.units import is_supported_unit
from scopecat.kernel.value_types import Array, Complex, Scalar
from scopecat.kernel.value_types import Quantity as QuantityType


@dataclass(frozen=True, slots=True)
class Unit:
    """An exact unit contract in ``Annotated``; it does not convert stored data."""

    name: str

    def __post_init__(self) -> None:
        if not is_supported_unit(self.name):
            raise ValueError(f"unsupported annotation unit: {self.name}")


def value_annotation_metadata(
    metadata: Sequence[object],
) -> tuple[Scalar | Array | None, Unit | None]:
    contracts = [item for item in metadata if isinstance(item, Scalar | Array)]
    units = [item for item in metadata if isinstance(item, Unit)]
    if len(contracts) + len(units) > 1:
        raise TypeError(
            "use one Unit or ScalarType/ArrayType annotation, not multiple contracts"
        )
    return (contracts[0] if contracts else None, units[0] if units else None)


def unit_scalar_type(native: object, unit: Unit) -> Scalar:
    if native is complex:
        return Scalar(Complex(unit=unit.name))
    if native is Quantity:
        return Scalar(QuantityType(unit=unit.name))
    raise TypeError(
        "compute Unit annotations require complex or Quantity; "
        "use Quantity for real values with units, and ArrayType for array dimensions"
    )
