"""作者自己的 NumPy/SciPy 扫频分析;只验证独立合成采集的一致性。"""

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import curve_fit

import scopecat as sc
from scopecat.measurements.dataset import Dataset


@dataclass(frozen=True)
class PeakFit:
    status: Literal["estimated", "rejected"]
    frequency: sc.Quantity | None
    contrast: float
    reason: str


@dataclass(frozen=True)
class CurvePoint:
    frequency: float = field(metadata={"unit": "GHz", "role": "coordinate"})
    measured: float
    fitted: float | None


@dataclass(frozen=True)
class Verification:
    accepted: bool
    error_ghz: float | None
    reason: str


def response(
    frequency: NDArray[np.float64],
    offset: float,
    gain: float,
    center: float,
    width: float,
) -> NDArray[np.float64]:
    return offset + gain / (1 + ((frequency - center) / width) ** 2)


def estimate(
    data: Dataset, *, minimum_contrast: float = 0.1
) -> tuple[PeakFit, list[CurvePoint]]:
    aligned = data.project(
        {"frequency": "frequency", "iq": "iq"}, units={"frequency": "GHz"}
    ).to_xarray(dims={"iq": ("shot",)})
    x = np.asarray(aligned["frequency"].values, dtype=float)
    mean_iq = np.asarray(aligned["iq"].mean(dim="shot").values, dtype=np.complex128)
    y = mean_iq.real
    if x.ndim != 1 or y.shape != x.shape or x.size < 5:
        raise ValueError("需要至少五个对齐的频率点及每点 IQ shots")
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("频率或 IQ 存在不可用值,请检查原始数据")
    contrast = float(np.ptp(y))
    result = PeakFit("rejected", None, contrast, "响应太平,请检查响应与扫描范围")
    prediction = None
    peak = int(np.argmax(y))
    if contrast >= minimum_contrast:
        if peak in (0, len(x) - 1):
            result = PeakFit(
                "rejected", None, contrast, "峰在范围边缘,请扩大或移动扫描范围"
            )
        else:
            span = float(np.ptp(x))
            try:
                coefficients, _ = curve_fit(
                    response,
                    x,
                    y,
                    p0=(float(y.min()), contrast, float(x[peak]), span / 10),
                    bounds=(
                        (-np.inf, 0, float(x.min()), span / 1000),
                        (np.inf, np.inf, float(x.max()), span),
                    ),
                )
            except (RuntimeError, ValueError) as error:
                result = PeakFit("rejected", None, contrast, f"拟合未收敛:{error}")
            else:
                prediction = response(x, *coefficients)
                residual = float(np.sqrt(np.mean((y - prediction) ** 2)) / contrast)
                if residual > 0.1:
                    result = PeakFit(
                        "rejected", None, contrast, "残差过大,请检查模型或原始数据"
                    )
                else:
                    result = PeakFit(
                        "estimated",
                        sc.Quantity(float(coefficients[2]), "GHz"),
                        contrast,
                        "教学候选,尚未独立验证",
                    )
    rows = [
        CurvePoint(
            float(a), float(b), None if prediction is None else float(prediction[i])
        )
        for i, (a, b) in enumerate(zip(x, y, strict=True))
    ]
    return result, rows


@sc.analysis_function
def fit_peak(
    data: Dataset, *, minimum_contrast: float = 0.1
) -> sc.AnalysisProducts[PeakFit]:
    result, curve = estimate(data, minimum_contrast=minimum_contrast)
    plots = [
        sc.AnalysisPlot(
            dataset="curve", x="frequency", y="measured", kind="scatter", id="measured"
        )
    ]
    if result.frequency is not None:
        plots.append(
            sc.AnalysisPlot(dataset="curve", x="frequency", y="fitted", id="fit")
        )
    return sc.AnalysisProducts(result, datasets={"curve": curve}, plots=tuple(plots))


DEFAULT_MAXIMUM_ERROR = sc.Quantity(1, "MHz")


@sc.analysis_function
def verify_peak(
    data: Dataset,
    *,
    expected_frequency: sc.Quantity,
    maximum_error: sc.Quantity = DEFAULT_MAXIMUM_ERROR,
) -> Verification:
    fitted, _ = estimate(data)
    if fitted.frequency is None:
        return Verification(False, None, fitted.reason)
    error = abs(fitted.frequency.to("GHz").value - expected_frequency.to("GHz").value)
    return Verification(
        error <= maximum_error.to("GHz").value,
        error,
        "独立合成采集的峰位置一致性;不是物理门保真度验证",
    )
