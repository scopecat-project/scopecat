"""Cross-run evidence for accepting a fitted DRAG-beta candidate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast

import scopecat as sc
from scopecat.api.run import RunHandle
from scopecat.measurements.results import Dataset

from reference_lab.workflows.drag_beta_analysis import drag_beta_observation_frame
from reference_lab.workflows.drag_beta_experiment import DragBetaQubit

DRAG_BETA_VERIFICATION_KEY = "drag-beta-candidate-verification"
DRAG_BETA_MINIMUM_IMPROVEMENT = 0.001
_DRAG_BETA_VERIFICATION_MODEL_ID = "reference_lab.drag_beta.verification.v1"


@dataclass(frozen=True, slots=True)
class DragBetaVerification:
    """Decision from the same amplified-leakage scan before and after fitting."""

    baseline_mean_probability_1: Annotated[
        float,
        sc.AnalysisField(label="Baseline mean P(1)", unit="ratio"),
    ]
    candidate_mean_probability_1: Annotated[
        float,
        sc.AnalysisField(label="Candidate mean P(1)", unit="ratio"),
    ]
    improvement: Annotated[
        float,
        sc.AnalysisField(label="P(1) reduction", unit="ratio"),
    ]
    minimum_improvement: Annotated[
        float,
        sc.AnalysisField(label="Required reduction", unit="ratio"),
    ]
    accepted: Annotated[bool, sc.AnalysisField(label="Accept candidate")]


@dataclass(frozen=True, slots=True)
class DragBetaComparison:
    """One run's score in a candidate verification."""

    role: Annotated[
        str,
        sc.AnalysisField(role="coordinate", label="Run role"),
    ]
    run_id: Annotated[str, sc.AnalysisField(label="Run")]
    mean_probability_1: Annotated[
        float,
        sc.AnalysisField(label="Mean P(1)", unit="ratio"),
    ]


DRAG_BETA_VERIFICATION_SCHEMA = sc.AnalysisFactSchema(
    _DRAG_BETA_VERIFICATION_MODEL_ID,
    DragBetaVerification,
)


def evaluate_drag_beta_candidate(
    baseline: Dataset,
    candidate: Dataset,
    *,
    qubit: DragBetaQubit = "q0",
    minimum_improvement: float = DRAG_BETA_MINIMUM_IMPROVEMENT,
) -> DragBetaVerification:
    """Compare mean amplified leakage across the standard calibration scan."""

    baseline_score = _mean_probability_one(baseline, qubit=qubit)
    candidate_score = _mean_probability_one(candidate, qubit=qubit)
    improvement = baseline_score - candidate_score
    return DragBetaVerification(
        baseline_mean_probability_1=baseline_score,
        candidate_mean_probability_1=candidate_score,
        improvement=improvement,
        minimum_improvement=minimum_improvement,
        accepted=improvement >= minimum_improvement,
    )


@sc.analysis_step(id=DRAG_BETA_VERIFICATION_KEY)
def drag_beta_candidate_verification(
    context: sc.AnalysisContext,
    *,
    baseline_run: RunHandle,
    candidate_run: RunHandle,
    qubit: DragBetaQubit = "q0",
    minimum_improvement: float = DRAG_BETA_MINIMUM_IMPROVEMENT,
) -> sc.Analysis:
    """Author one project analysis over exact baseline and candidate datasets."""

    baseline = context.measurements(
        baseline_run,
        id="baseline",
        role="baseline",
        title="Baseline DRAG scan",
    )
    candidate = context.measurements(
        candidate_run,
        id="candidate",
        role="candidate",
        title="Candidate DRAG scan",
    )
    decision = evaluate_drag_beta_candidate(
        baseline,
        candidate,
        qubit=qubit,
        minimum_improvement=minimum_improvement,
    )
    comparison = (
        DragBetaComparison(
            role="baseline",
            run_id=baseline_run.id,
            mean_probability_1=decision.baseline_mean_probability_1,
        ),
        DragBetaComparison(
            role="candidate",
            run_id=candidate_run.id,
            mean_probability_1=decision.candidate_mean_probability_1,
        ),
    )
    return (
        context.result()
        .dataset(
            "comparison",
            comparison,
            title="Baseline and candidate leakage",
        )
        .fact(
            "decision",
            decision,
            schema=DRAG_BETA_VERIFICATION_SCHEMA,
            title="DRAG beta candidate decision",
        )
        .table(
            dataset="comparison",
            id="comparison-table",
            title="DRAG beta candidate comparison",
        )
        .artifact(
            "verification-report",
            text=_verification_report(decision),
            filename="drag-beta-verification.md",
            media_type="text/markdown",
            title="DRAG beta verification report",
        )
    )


def _mean_probability_one(
    dataset: Dataset,
    *,
    qubit: DragBetaQubit,
) -> float:
    frame = drag_beta_observation_frame(dataset, qubit=qubit)
    value = cast("float | None", frame.get_column("probability_1").mean())
    if value is None:
        raise ValueError("DRAG beta verification requires measured probabilities")
    return value


def _verification_report(decision: DragBetaVerification) -> str:
    verdict = "accepted" if decision.accepted else "rejected"
    return (
        "# DRAG beta candidate verification\n\n"
        f"Verdict: {verdict}\n\n"
        f"- Baseline mean P(1): {decision.baseline_mean_probability_1:.9g}\n"
        f"- Candidate mean P(1): {decision.candidate_mean_probability_1:.9g}\n"
        f"- Reduction: {decision.improvement:.9g}\n"
        f"- Required reduction: {decision.minimum_improvement:.9g}\n"
    )


__all__ = [
    "DRAG_BETA_MINIMUM_IMPROVEMENT",
    "DRAG_BETA_VERIFICATION_KEY",
    "DRAG_BETA_VERIFICATION_SCHEMA",
    "DragBetaComparison",
    "DragBetaVerification",
    "drag_beta_candidate_verification",
    "evaluate_drag_beta_candidate",
]
