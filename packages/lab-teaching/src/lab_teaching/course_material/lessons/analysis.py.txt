"""Retained scientific diagnostics for the synthetic teaching acquisition."""

from dataclasses import dataclass
from typing import cast

import numpy as np

import scopecat as sc


@dataclass(frozen=True)
class Diagnostic:
    status: str
    selected_value: float | None
    failure: str | None


DIAGNOSTIC = sc.AnalysisFactSchema("teaching.rabi-diagnostic.v1", Diagnostic)


@sc.analysis_step(id="teaching.rabi-diagnostic")
def rabi_diagnostic(context: sc.AnalysisContext) -> sc.Analysis:
    measurements = context.measurements()
    amplitudes = tuple(
        float(cast("float", value))
        for value in measurements["amplitude"].require_values()
    )
    shots = tuple(
        np.asarray(row, dtype=np.complex128)
        for row in measurements["iq"].require_values()
    )
    # This lesson models only its declared synthetic sine response. Real laboratory
    # fitting, acceptance thresholds and parameter publication remain lab policy.
    centers = np.asarray([np.mean(row).real for row in shots], dtype=np.float64)
    axis = np.asarray(amplitudes, dtype=np.float64)
    contrast = float(np.ptp(centers))
    if contrast < 0.05:
        diagnostic = Diagnostic("fit_failed", None, "No resolved synthetic response.")
    else:
        candidates = np.linspace(0.05, 0.8, 1501)
        residuals: list[float] = []
        for candidate in candidates:
            wave = np.sin(np.pi * axis / (2 * candidate)) ** 2
            design = np.column_stack((wave, np.ones_like(wave)))
            coefficients, *_ = np.linalg.lstsq(design, centers, rcond=None)
            residuals.append(
                float(cast("float", np.mean((design @ coefficients - centers) ** 2)))
            )
        selected = float(cast("float", candidates[int(np.argmin(residuals))]))
        covered = float(np.ptp(axis)) >= 2 * selected
        diagnostic = Diagnostic(
            "passed" if covered else "insufficient_range",
            selected if covered else None,
            None if covered else "Expand the scan to cover a complete oscillation.",
        )
    return (
        context.result("Synthetic Rabi diagnostic")
        .fact("diagnostic", diagnostic, schema=DIAGNOSTIC)
        .fact("evidence_kind", "synthetic-computation")
    )
