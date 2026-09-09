"""User-editable retained-signal fitting model; refresh this authored source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray
from scopecat.application.comparison import ComparisonModel, ComparisonParameter
from scopecat.kernel.quantity import Quantity
from scopecat.measurements.dataset import Dataset

MODEL = ComparisonModel(
    id="signal-quadratic",
    version="1",
    title="Signal quadratic fit",
    description=(
        "Fit one quadratic to explicitly selected points from two retained "
        "signal runs. Offset adjusts the suggested carrier; this is an "
        "analytic example, not calibration validity or default activation."
    ),
    coordinate="frequency",
    observable="response",
    parameters=(
        ComparisonParameter(
            name="offset_ghz",
            label="Carrier offset (GHz)",
            default=0,
            minimum=-0.1,
            maximum=0.1,
        ),
    ),
)


@dataclass(frozen=True)
class SignalFit:
    model_id: str
    model_version: str
    primary_run: str
    secondary_run: str
    primary_hash: str
    secondary_hash: str
    primary_points: tuple[int, ...]
    secondary_points: tuple[int, ...]
    offset_ghz: float
    center_ghz: float
    coefficients: tuple[float, ...]


def fit_signal(
    primary: Dataset,
    secondary: Dataset,
    primary_points: tuple[int, ...],
    secondary_points: tuple[int, ...],
    primary_run: str,
    secondary_run: str,
    offset_ghz: float,
) -> SignalFit:
    """Project-owned model; selection and all model inputs are trace bindings."""
    xs: list[float] = []
    ys: list[float] = []
    for dataset, points in ((primary, primary_points), (secondary, secondary_points)):
        selected = dataset.isel(point=list(points))
        frequency, response = selected[MODEL.coordinate], selected[MODEL.observable]
        for x, y in zip(frequency.values, response.values, strict=True):
            assert isinstance(x, float | int) and isinstance(y, float | int)
            assert frequency.unit is not None and response.unit is not None
            xs.append(Quantity(float(x), frequency.unit).to("GHz").value)
            ys.append(Quantity(float(y), response.unit).to("V").value)
    if len(set(xs)) < 3:
        raise ValueError("Quadratic fit requires at least three distinct frequencies")
    fitted = cast("NDArray[np.float64]", np.polynomial.polynomial.polyfit(xs, ys, 2))
    coefficients = tuple(float(cast("np.float64", fitted[index])) for index in range(3))
    if coefficients[2] >= 0:
        raise ValueError("Selected data do not identify a concave signal peak")
    center = -coefficients[1] / (2 * coefficients[2]) + offset_ghz
    if center < min(xs) or center > max(xs):
        raise ValueError("Suggested carrier lies outside the selected frequency range")
    return SignalFit(
        MODEL.id,
        MODEL.version,
        primary_run,
        secondary_run,
        primary.entry.content_hash,
        secondary.entry.content_hash,
        primary_points,
        secondary_points,
        offset_ghz,
        center,
        coefficients,
    )
