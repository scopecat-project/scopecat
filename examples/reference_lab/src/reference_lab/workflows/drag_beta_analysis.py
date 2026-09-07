"""Quadratic fitting and review evidence for the DRAG-beta workflow."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, SupportsFloat, cast

import numpy as np
import polars as pl
import scopecat as sc
from scopecat import Quantity
from scopecat.measurements.results import Dataset
from scopecat.records.analysis import (
    AnalysisDatasetViewSource,
    AnalysisFigureLayerSpec,
    AnalysisFigureProjection,
    AnalysisUncertaintyProjection,
)

from reference_lab.parameters import Q0_DRAG_BETA, Q1_DRAG_BETA
from reference_lab.workflows.drag_beta_calibration import (
    NEGATIVE_CANDIDATE_ID,
    POSITIVE_CANDIDATE_ID,
)
from reference_lab.workflows.drag_beta_experiment import (
    DragBetaQubit,
    drag_beta_experiment,
)

_DRAG_BETA_FIT_MODEL_ID = "reference_lab.drag_beta.shared_n2_quadratic.v1"
_DRAG_BETA_ANALYSIS_KEY = "drag-beta-calibration"
_DRAG_BETA_PARAMETERS = {
    "q0": Q0_DRAG_BETA,
    "q1": Q1_DRAG_BETA,
}
_BETA_FIELD = sc.AnalysisField(
    id="beta_ns",
    role="coordinate",
    label="DRAG beta",
    unit="ns",
)
_AMPLIFICATION_FIELD = sc.AnalysisField(
    role="coordinate",
    label="Amplification",
)
_PROBABILITY_FIELD = sc.AnalysisField(
    id="probability_1",
    label="P(1)",
    unit="ratio",
)


@dataclass(frozen=True, slots=True)
class DragBetaObservation:
    """One measured probability at a beta and amplification count."""

    beta: Annotated[
        Quantity,
        _BETA_FIELD,
    ]
    amplification: Annotated[int, _AMPLIFICATION_FIELD]
    p1: Annotated[
        float,
        _PROBABILITY_FIELD,
    ]


@dataclass(frozen=True, slots=True)
class DragBetaFit:
    """Fit of ``p1 = baseline + N²(a beta² + b beta + c)``."""

    beta_hat: Annotated[
        Quantity,
        sc.AnalysisField(label="Selected beta", unit="ns"),
    ]
    baseline: Annotated[float, sc.AnalysisField(label="Baseline")]
    quadratic: Annotated[
        float,
        sc.AnalysisField(label="Quadratic coefficient"),
    ]
    linear: Annotated[float, sc.AnalysisField(label="Linear coefficient")]
    scaled_offset: Annotated[float, sc.AnalysisField(label="Scaled offset")]
    rmse: Annotated[float, sc.AnalysisField(label="RMSE")]
    model_id: Annotated[str, sc.AnalysisField(label="Fit model")] = (
        _DRAG_BETA_FIT_MODEL_ID
    )


@dataclass(frozen=True, slots=True)
class DragBetaCurvePoint:
    beta: Annotated[Quantity, _BETA_FIELD]
    amplification: Annotated[int, _AMPLIFICATION_FIELD]
    p1: Annotated[float, _PROBABILITY_FIELD]
    lower: Annotated[float, sc.AnalysisField(unit="ratio")]
    upper: Annotated[float, sc.AnalysisField(unit="ratio")]


def drag_beta_fit_curve(
    observations: Sequence[DragBetaObservation], fit: DragBetaFit
) -> tuple[DragBetaCurvePoint, ...]:
    """Dense fitted curves with one RMSE residual scale, not confidence intervals."""
    betas = [_beta_ns(point.beta) for point in observations]
    result: list[DragBetaCurvePoint] = []
    for amplification in sorted({point.amplification for point in observations}):
        for beta in np.linspace(min(betas), max(betas), 81):
            value = fit.baseline + amplification**2 * (
                fit.quadratic * float(beta) ** 2
                + fit.linear * float(beta)
                + fit.scaled_offset
            )
            result.append(
                DragBetaCurvePoint(
                    Quantity(float(beta), "ns"),
                    amplification,
                    value,
                    value - fit.rmse,
                    value + fit.rmse,
                )
            )
    return tuple(result)


DRAG_BETA_FIT_SCHEMA = sc.AnalysisFactSchema(
    _DRAG_BETA_FIT_MODEL_ID,
    DragBetaFit,
)


def fit_drag_beta(observations: Sequence[DragBetaObservation]) -> DragBetaFit:
    """Fit the shared quadratic and return its minimum and residual."""

    selected = tuple(observations)
    if len(selected) < 4:
        raise ValueError("DRAG-beta fitting requires at least four observations")

    design = np.asarray(
        [
            (
                1.0,
                observation.amplification**2 * _beta_ns(observation.beta) ** 2,
                observation.amplification**2 * _beta_ns(observation.beta),
                float(observation.amplification**2),
            )
            for observation in selected
        ],
        dtype=float,
    )
    response = np.asarray([observation.p1 for observation in selected], dtype=float)
    coefficients, _residuals, rank, _singular_values = np.linalg.lstsq(
        design,
        response,
        rcond=None,
    )
    if int(rank) != 4:
        raise ValueError("DRAG-beta observations do not identify a joint quadratic")

    baseline, quadratic, linear, scaled_offset = (
        float(value) for value in coefficients
    )
    if quadratic <= 0:
        raise ValueError("DRAG-beta quadratic must have positive curvature")

    beta_hat = -linear / (2.0 * quadratic)
    scanned_betas = tuple(_beta_ns(observation.beta) for observation in selected)
    if not min(scanned_betas) <= beta_hat <= max(scanned_betas):
        raise ValueError("fitted DRAG beta lies outside the scanned range")

    residual = design @ coefficients - response
    rmse = float(cast("SupportsFloat", np.sqrt(np.mean(residual**2))))
    return DragBetaFit(
        beta_hat=Quantity(beta_hat, "ns"),
        baseline=baseline,
        quadratic=quadratic,
        linear=linear,
        scaled_offset=scaled_offset,
        rmse=rmse,
    )


@sc.analysis_step(id=_DRAG_BETA_ANALYSIS_KEY)
def drag_beta_analysis(
    context: sc.AnalysisContext,
    *,
    qubit: DragBetaQubit = "q0",
) -> sc.Analysis:
    """Fit one DRAG run and author its table, figure, and proposal."""

    observations = _observations_from_frame(
        drag_beta_observation_frame(context.measurements(), qubit=qubit)
    )
    fit = fit_drag_beta(observations)

    return (
        context.result(f"{qubit} DRAG beta calibration")
        .dataset(
            "observations",
            observations,
            title="DRAG beta observations",
        )
        .fact(
            "quadratic-fit",
            fit,
            schema=DRAG_BETA_FIT_SCHEMA,
            title="DRAG beta quadratic fit",
        )
        .table(
            dataset="observations",
            id="observations-table",
            title="DRAG beta observations",
        )
        .dataset(
            "fit-curve",
            drag_beta_fit_curve(observations, fit),
            title="Fitted DRAG curves and residual bounds",
        )
        .figure_layers(
            id="observations-by-amplification",
            title="Measured DRAG probabilities and quadratic fit",
            layers=(
                AnalysisFigureLayerSpec(
                    id="measured",
                    source=AnalysisDatasetViewSource(output_id="observations"),
                    projection=AnalysisFigureProjection(
                        kind="scatter",
                        x="beta_ns",
                        y="probability_1",
                        series="amplification",
                    ),
                ),
                AnalysisFigureLayerSpec(
                    id="fit",
                    source=AnalysisDatasetViewSource(output_id="fit-curve"),
                    projection=AnalysisFigureProjection(
                        kind="line",
                        x="beta_ns",
                        y="probability_1",
                        series="amplification",
                        uncertainty=AnalysisUncertaintyProjection(
                            lower="lower",
                            upper="upper",
                            style="band",
                            meaning=(
                                "Plus/minus one RMSE residual scale; "
                                "not a confidence interval"
                            ),
                        ),
                    ),
                ),
            ),
        )
        .artifact(
            "fit-report",
            text=_drag_beta_report(observations, fit),
            filename="drag-beta-fit.md",
            media_type="text/markdown",
            title="DRAG beta fit report",
        )
        .propose(
            f"{qubit}-drag-beta",
            _DRAG_BETA_PARAMETERS[qubit].update(fit.beta_hat),
            reason=(
                f"Shared N² quadratic fit selected the {qubit} DRAG beta used by "
                f"{POSITIVE_CANDIDATE_ID!r} and {NEGATIVE_CANDIDATE_ID!r}; "
                f"RMSE={fit.rmse:.6g}."
            ),
            evidence=("quadratic-fit", "observations"),
        )
    )


def drag_beta_observation_frame(
    dataset: Dataset,
    *,
    qubit: DragBetaQubit = "q0",
) -> pl.DataFrame:
    """Project one DRAG run into the canonical comparison columns."""

    schema = drag_beta_experiment(qubit).output
    frame = (
        dataset.bind(schema)
        .project(
            {
                "beta_ns": schema.beta,
                "amplification": schema.amplification,
                "probability_1": schema.probabilities.probability_1,
            },
            units={"beta_ns": "ns"},
            identity=False,
        )
        .to_polars()
    )
    return frame


def _observations_from_frame(
    frame: pl.DataFrame,
) -> tuple[DragBetaObservation, ...]:
    return tuple(
        DragBetaObservation(
            beta=Quantity(beta_ns, "ns"),
            amplification=amplification,
            p1=probability_1,
        )
        for beta_ns, amplification, probability_1 in cast(
            "list[tuple[float, int, float]]",
            frame.select(("beta_ns", "amplification", "probability_1")).rows(),
        )
    )


def _drag_beta_report(
    observations: Sequence[DragBetaObservation],
    fit: DragBetaFit,
) -> str:
    return (
        "# DRAG beta quadratic fit\n\n"
        f"Observations: {len(observations)}\n\n"
        f"- Selected beta: {_beta_ns(fit.beta_hat):.9g} ns\n"
        f"- RMSE: {fit.rmse:.9g}\n"
        f"- Model: {fit.model_id}\n"
    )


def _beta_ns(value: Quantity) -> float:
    selected = float(value.to("ns").value)
    if not math.isfinite(selected):
        raise ValueError("DRAG beta must be finite")
    return selected


__all__ = [
    "DragBetaFit",
    "DragBetaObservation",
    "drag_beta_analysis",
    "drag_beta_observation_frame",
    "fit_drag_beta",
]
