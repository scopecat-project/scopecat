"""单条合成扫频曲线的普通 NumPy 分析; 不是物理标定算法。"""

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

import scopecat as sc
from scopecat.measurements.dataset import Dataset


@dataclass(frozen=True)
class CurveSummary:
    status: Literal["estimated", "no_response"]
    peak_sample: sc.Quantity | None
    contrast: float
    peak_frequency_uncertainty: sc.Quantity | None


@dataclass(frozen=True)
class CurvePoint:
    frequency: float = field(metadata={"unit": "GHz", "role": "coordinate"})
    mean_i: float
    shot_standard_error: float


@sc.analysis_function
def summarize_curve(
    data: Dataset, *, minimum_contrast: float = 0.1
) -> sc.AnalysisProducts[CurveSummary]:
    aligned = data.project(
        {"frequency": "frequency", "iq": "iq"}, units={"frequency": "GHz"}
    ).to_xarray(dims={"iq": ("shot",)})
    x = np.asarray(aligned["frequency"].values, dtype=float)
    shots = np.asarray(aligned["iq"].values, dtype=np.complex128).real
    if x.ndim != 1 or shots.ndim != 2 or len(x) != len(shots) or len(x) < 3:
        raise ValueError("每组需要至少三个频率点, 并保留每点的 shot 轴")
    if shots.shape[1] < 2 or not np.isfinite(x).all() or not np.isfinite(shots).all():
        raise ValueError("需要可用的频率和至少两个原始 shots, 不能由平均 IQ 重建误差")
    y = shots.mean(axis=1)
    errors = shots.std(axis=1, ddof=1) / np.sqrt(shots.shape[1])
    contrast = float(np.ptp(y))
    peak = sc.Quantity(float(x[int(np.argmax(y))]), "GHz")
    result = CurveSummary(
        "estimated" if contrast >= minimum_contrast else "no_response",
        peak if contrast >= minimum_contrast else None,
        contrast,
        None,  # shot 均值的标准误差不是峰频率的拟合不确定度。
    )
    return sc.AnalysisProducts(
        result,
        datasets={
            "curve": [
                CurvePoint(float(frequency), float(value), float(error))
                for frequency, value, error in zip(x, y, errors, strict=True)
            ]
        },
        plots=(sc.AnalysisPlot(dataset="curve", x="frequency", y="mean_i"),),
    )
