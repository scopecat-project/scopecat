# pyright: reportAny=false
"""Ordinary NumPy analysis of retained synthetic response; no hardware claims."""

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import scopecat as sc
from scopecat.measurements.dataset import Dataset


@dataclass(frozen=True)
class PeakResult:
    status: Literal["estimated", "no_response", "fit_rejected"]
    frequency: sc.Quantity | None
    contrast: float


@dataclass(frozen=True)
class CurvePoint:
    frequency: float = field(metadata={"unit": "GHz", "role": "coordinate"})
    response: float


@sc.analysis_function
def estimate_peak(
    data: Dataset, *, minimum_contrast: float = 0.2
) -> sc.AnalysisProducts[PeakResult]:
    """A local quadratic peak fit; it is not independently verified calibration."""
    selected = data.project(
        {"frequency": "frequency", "response": "result"}, units={"frequency": "GHz"}
    ).to_xarray()
    x = np.asarray(selected["frequency"].values, dtype=np.float64)
    y = np.asarray(selected["response"].values, dtype=np.float64)
    if x.ndim != 1 or y.shape != x.shape or len(x) < 3:
        raise ValueError(
            "peak estimate needs aligned scalar points and at least 3 observations"
        )
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("peak estimate requires finite available input points")
    contrast = float(np.ptp(y))
    result = PeakResult("no_response", None, contrast)
    if contrast >= minimum_contrast:
        peak = int(np.argmax(y))
        result = PeakResult("fit_rejected", None, contrast)
        if 0 < peak < len(x) - 1:
            origin = float(x[peak])
            local_x = x[peak - 1 : peak + 2] - origin
            local_y = y[peak - 1 : peak + 2]
            quadratic, linear, _ = np.polyfit(local_x, local_y, 2)
            center = float(-linear / (2 * quadratic))
            if quadratic < 0 and local_x[0] <= center <= local_x[-1]:
                result = PeakResult(
                    "estimated", sc.Quantity(origin + center, "GHz"), contrast
                )
    return sc.AnalysisProducts(
        result,
        datasets={
            "curve": [CurvePoint(float(a), float(b)) for a, b in zip(x, y, strict=True)]
        },
        plots=(sc.AnalysisPlot(dataset="curve", x="frequency", y="response"),),
    )
