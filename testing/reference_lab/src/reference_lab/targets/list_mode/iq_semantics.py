"""Canonical integrated-IQ convention shared by target and virtual device."""

from __future__ import annotations

import math
from collections.abc import Sequence
from functools import lru_cache
from typing import cast

import numpy as np
from numpy.typing import NDArray

INTEGRATED_IQ_SEMANTICS_ID = "reference_lab.integrated_iq.ssb_midpoint.v1"


def integrate_rectangular_iq(
    trace: Sequence[float] | NDArray[np.float64],
    *,
    start_sample: int,
    sample_count: int,
    sample_rate_hz: float,
    demodulation_frequency_hz: float,
) -> complex:
    """Demodulate at sample centers using exp(-iωt) and SSB amplitude scaling.

    The result retains the trace unit. Non-zero IFs receive a factor of two;
    zero IF retains unity normalization. The rectangular window is averaged by
    its exact sample count.
    """

    normalization = 1.0 if demodulation_frequency_hz == 0.0 else 2.0
    samples = np.asarray(
        trace[start_sample : start_sample + sample_count],
        dtype=np.float64,
    )
    weights = _demodulation_weights(
        start_sample,
        sample_count,
        demodulation_frequency_hz,
        sample_rate_hz,
    )
    result = cast(
        "np.complex128",
        normalization * np.dot(samples, weights) / sample_count,
    )
    return complex(result)


@lru_cache(maxsize=64)
def _demodulation_weights(
    start_sample: int,
    sample_count: int,
    demodulation_frequency_hz: float,
    sample_rate_hz: float,
) -> NDArray[np.complex128]:
    sample_indices = np.arange(
        start_sample,
        start_sample + sample_count,
        dtype=np.float64,
    )
    weights = np.ascontiguousarray(
        np.exp(
            -1j
            * math.tau
            * demodulation_frequency_hz
            * (sample_indices + 0.5)
            / sample_rate_hz
        ),
        dtype=np.complex128,
    )
    weights.flags.writeable = False
    return weights


__all__ = ["INTEGRATED_IQ_SEMANTICS_ID", "integrate_rectangular_iq"]
