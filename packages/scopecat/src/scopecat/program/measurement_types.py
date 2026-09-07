"""Static measurement value types shared by programs and durable records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from scopecat.kernel.value_types import (
    Bool,
    Complex,
    Entity,
    Float,
    Int,
    Scalar,
    String,
    ValueDType,
)
from scopecat.kernel.value_types import Quantity as QuantityType

type MeasurementVariableRole = Literal["coordinate", "observable"]
type MeasurementDType = ValueDType
type EntityAcquisitionPolicy = Literal[
    "independent",
    "best_effort",
    "all_or_nothing",
]
type MeasurementArrayElement = (
    np.bool_ | np.int64 | np.float64 | np.complex128 | np.str_
)
type MeasurementArrayData = NDArray[MeasurementArrayElement]
type MeasurementSegmentedData = tuple[MeasurementArrayData | None, ...]
type NativeMeasurementScalar = bool | int | float | complex | str
type NativeMeasurementValue = (
    NativeMeasurementScalar | MeasurementArrayData | MeasurementSegmentedData
)


@dataclass(frozen=True, slots=True)
class EntityAcquisitionSemantics:
    """Execution semantics distinct from one record's entity-array layout."""

    policy: EntityAcquisitionPolicy = "independent"
    cohort_id: str | None = None

    def __post_init__(self) -> None:
        if self.policy == "independent":
            if self.cohort_id is not None:
                raise ValueError("independent entity acquisition has no cohort id")
            return
        if not self.cohort_id:
            raise ValueError(f"{self.policy} entity acquisition requires a cohort id")


def measurement_value_spec_from_scalar(
    value_type: Scalar,
) -> tuple[MeasurementDType, str | None]:
    """Project one program scalar type into its measurement schema."""

    atom = value_type.atom
    if isinstance(atom, Bool):
        return "bool", None
    if isinstance(atom, Int):
        return "int64", None
    if isinstance(atom, Float):
        return "float64", None
    if isinstance(atom, Complex):
        return "complex128", atom.unit
    if isinstance(atom, QuantityType):
        return "float64", atom.unit
    if isinstance(atom, String | Entity):
        return "string", None
    raise TypeError("opaque payload scalars cannot be represented in a dataset")


__all__ = [
    "EntityAcquisitionPolicy",
    "EntityAcquisitionSemantics",
    "MeasurementArrayData",
    "MeasurementArrayElement",
    "MeasurementDType",
    "MeasurementSegmentedData",
    "MeasurementVariableRole",
    "NativeMeasurementScalar",
    "NativeMeasurementValue",
    "measurement_value_spec_from_scalar",
]
