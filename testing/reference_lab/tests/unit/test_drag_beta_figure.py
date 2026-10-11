from __future__ import annotations

import pytest
from scopecat import Quantity

from reference_lab.workflows.drag_beta_analysis import (
    DragBetaFit,
    DragBetaObservation,
    drag_beta_fit_curve,
)


def test_fit_curve_retains_amplification_and_declares_residual_scale_bounds() -> None:
    observations = (
        DragBetaObservation(Quantity(-1.0, "ns"), 1, 0.2),
        DragBetaObservation(Quantity(1.0, "ns"), 1, 0.3),
        DragBetaObservation(Quantity(0.0, "ns"), 2, 0.4),
    )
    fit = DragBetaFit(
        beta_hat=Quantity(0.0, "ns"),
        baseline=0.1,
        quadratic=0.02,
        linear=0.0,
        scaled_offset=0.01,
        rmse=0.005,
    )
    curve = drag_beta_fit_curve(observations, fit)
    assert len(curve) == 162
    for amplification in (1, 2):
        points = [point for point in curve if point.amplification == amplification]
        assert points[0].beta == Quantity(-1.0, "ns")
        assert points[-1].beta == Quantity(1.0, "ns")
        center = points[40]
        assert center.beta == Quantity(0.0, "ns")
        assert center.p1 == pytest.approx(0.1 + amplification**2 * 0.01)
        assert center.lower == pytest.approx(center.p1 - 0.005)
        assert center.upper == pytest.approx(center.p1 + 0.005)
