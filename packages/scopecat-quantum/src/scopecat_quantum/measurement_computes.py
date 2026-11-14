# pyright: reportPrivateUsage=false
"""Hardware-independent point-local measurement computes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Annotated, Literal, cast

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, model_validator
from scopecat import (
    ArrayDimension,
    ArrayType,
    ComplexType,
    ExperimentContext,
    IntType,
    ModuleContext,
    ProductBundle,
    ProductRef,
    QuantityType,
    Result,
    ScalarType,
    constant,
)

_BINARY_IQ_DISCRIMINATOR_SCHEMA = "scopecat-quantum.binary-iq-discriminator.v1"


@dataclass(frozen=True, slots=True)
class IqShotStatistics(ProductBundle):
    """Count and coherent mean of one complete, finite integrated-IQ shot array.

    Returning this bundle records only the statistics. Return the input product
    alongside it to keep the original shots as well. No input is deleted.
    """

    count: Annotated[
        ProductRef[int],
        ScalarType(IntType(minimum=1)),
        Result(
            metadata={
                "statistic": "count",
                "reduction_axis": "shot",
                "scope": "one-acquisition",
                "nonfinite": "reject",
            }
        ),
    ]
    mean: Annotated[
        ProductRef[complex],
        ScalarType(ComplexType(unit="ratio")),
        Result(
            metadata={
                "statistic": "arithmetic-mean",
                "reduction_axis": "shot",
                "scope": "one-acquisition",
                "nonfinite": "reject",
            }
        ),
    ]


@IqShotStatistics.kernel
def _iq_shot_statistics(
    *,
    iq_shots: Annotated[
        NDArray[np.complex128],
        ArrayType(
            dtype="complex128",
            unit="ratio",
            dimensions=(ArrayDimension("shot", None, kind="shot", unit="count"),),
        ),
    ],
) -> tuple[int, complex]:
    if iq_shots.size == 0:
        raise ValueError("IQ shot statistics require at least one shot")
    if not np.isfinite(iq_shots).all():
        raise ValueError("IQ shot statistics require finite real and imaginary values")
    count = int(iq_shots.size)
    mean = complex(_component_mean(iq_shots.real), _component_mean(iq_shots.imag))
    return count, mean


def _component_mean(values: NDArray[np.float64]) -> float:
    # Normalize each component before averaging. Dividing by count first can
    # still overflow on summation or erase equal subnormal values.
    scale = float(cast("np.float64", np.max(np.abs(values))))
    return 0.0 if scale == 0 else float(np.mean(values / scale)) * scale


def iq_shot_statistics(
    context: ModuleContext | ExperimentContext,
    iq_shots: ProductRef,
    /,
    *,
    id: str = "iq-shot-statistics",
) -> IqShotStatistics:
    """Reduce one acquired complex128 ratio [shot/count] array at each point.

    All shots must be finite and the array must be nonempty. The count includes
    every shot; it is not a state-discrimination count. The complex mean retains
    phase, rather than averaging magnitudes. Unavailable inputs use the ordinary
    compute propagation rule. This has no cross-chunk state or device feedback.
    """
    if iq_shots.value_spec.unit != "ratio":
        raise TypeError("IQ shot statistics require explicit ratio units")
    return context.compute(id, fn=_iq_shot_statistics, iq_shots=iq_shots)


class IqCentroid(BaseModel):
    """One finite centroid in dimensionless integrated-IQ space."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    real: float
    imag: float
    unit: Literal["ratio"] = "ratio"

    @model_validator(mode="after")
    def validate_finite(self) -> IqCentroid:
        if not (math.isfinite(self.real) and math.isfinite(self.imag)):
            raise ValueError("binary IQ discriminator centroids must be finite")
        return self


class BinaryIqDiscriminator(BaseModel):
    """Nearest-centroid classification for binary integrated-IQ shots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state_0_centroid: IqCentroid
    state_1_centroid: IqCentroid
    tie_policy: Literal["state_0", "state_1"] = "state_0"

    @model_validator(mode="after")
    def validate_distinct_centroids(self) -> BinaryIqDiscriminator:
        state_0 = self.state_0_centroid
        state_1 = self.state_1_centroid
        if state_0.real == state_1.real and state_0.imag == state_1.imag:
            raise ValueError("binary IQ discriminator centroids must be distinct")
        return self


@dataclass(frozen=True, slots=True)
class BinaryIqProbabilityProducts(ProductBundle):
    """Typed products emitted by one binary IQ discrimination step."""

    probability_0: Annotated[
        ProductRef[float],
        ScalarType(QuantityType(unit="ratio")),
    ]
    probability_1: Annotated[
        ProductRef[float],
        ScalarType(QuantityType(unit="ratio")),
    ]


def binary_iq_probabilities(
    context: ModuleContext | ExperimentContext,
    iq_shots: ProductRef,
    /,
    *,
    discriminator: BinaryIqDiscriminator,
    id: str = "binary-iq-probability",
) -> BinaryIqProbabilityProducts:
    """Declare binary state probabilities independently at each scan point."""

    @BinaryIqProbabilityProducts.kernel
    def calculate(
        *,
        iq_shots: Annotated[
            NDArray[np.complex128],
            ArrayType(
                dtype="complex128",
                unit="ratio",
                dimensions=(ArrayDimension("shot", None, kind="shot", unit="count"),),
            ),
        ],
        discriminator: BinaryIqDiscriminator,
    ) -> tuple[float, float]:
        return _binary_iq_probability_value(
            np.asarray(iq_shots),
            discriminator,
        )

    return context.compute(
        id,
        fn=calculate,
        iq_shots=iq_shots,
        discriminator=constant(
            discriminator,
            schema=_BINARY_IQ_DISCRIMINATOR_SCHEMA,
        ),
    )


def _binary_iq_probability_value(
    value: NDArray[np.generic],
    discriminator: BinaryIqDiscriminator,
) -> tuple[float, float]:
    if value.dtype != np.dtype("complex128") or value.ndim != 1:
        raise ValueError("binary IQ compute requires complex128 [shot] values")
    if value.shape[0] <= 0:
        raise ValueError("binary IQ compute requires at least one shot")

    state_0 = complex(
        discriminator.state_0_centroid.real,
        discriminator.state_0_centroid.imag,
    )
    state_1 = complex(
        discriminator.state_1_centroid.real,
        discriminator.state_1_centroid.imag,
    )
    shots = cast("NDArray[np.complex128]", value)
    distance_0 = np.square(np.abs(shots - state_0))
    distance_1 = np.square(np.abs(shots - state_1))
    if discriminator.tie_policy == "state_0":
        state_0_count = int(np.count_nonzero(distance_0 <= distance_1))
    else:
        state_0_count = int(np.count_nonzero(distance_0 < distance_1))

    probability_0 = state_0_count / len(value)
    return probability_0, 1.0 - probability_0


__all__ = [
    "BinaryIqDiscriminator",
    "BinaryIqProbabilityProducts",
    "IqCentroid",
    "IqShotStatistics",
    "binary_iq_probabilities",
    "iq_shot_statistics",
]
