"""Lab-owned, adjustable quadratic fit of two retained signal-model runs.

This is a small analysis example, not a physical calibration or acquisition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

import numpy as np
import pandas as pd
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.api.comparison import comparison_inputs, save_comparison
from scopecat.api.lab import LabClient
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.application.comparison import ComparisonHandoff, ComparisonResult
from scopecat.kernel.quantity import Quantity
from scopecat.records.analysis import (
    AnalysisDatasetViewSource,
    AnalysisField,
    AnalysisFigureLayerSpec,
    AnalysisFigureProjection,
)
from scopecat.records.comparison import (
    ComparisonCatalog,
    ComparisonPublication,
    ComparisonRequest,
)
from scopecat.records.control_edit import ControlEdit
from scopecat.records.launch_request import LaunchRequest

from reference_lab.control_launch import CONTROL_ENTRY
from reference_lab.parameters import DRIVE_CARRIER_FREQUENCY, Q0
from reference_lab.workflows.authored.comparison import MODEL, SignalFit, fit_signal


@dataclass(frozen=True)
class SignalNextInput:
    experiment: str
    version: str
    frequency: Quantity


@dataclass(frozen=True)
class CandidateReview:
    accepted: bool
    candidate_run: str
    candidate_analysis: str
    candidate_publication_hash: str
    proposal_ids: tuple[str, ...]
    actor: str
    reason: str


NEXT_INPUT_SCHEMA = AnalysisFactSchema(
    "reference_lab.signal-next-input.v1", SignalNextInput
)
FIT_SCHEMA = AnalysisFactSchema("reference_lab.signal-comparison.v1", SignalFit)
REVIEW_SCHEMA = AnalysisFactSchema(
    "reference_lab.comparison-review.v1", CandidateReview
)


def _publication(run: str, published: PublishedAnalysis) -> ComparisonPublication:
    return ComparisonPublication(
        run_id=run,
        analysis_id=published.id,
        publication_hash=published.publication_hash,
    )


class _Observation(TypedDict):
    frequency: float
    response: float
    run: str
    point: int


def comparison_provider(lab: LabClient, request: ComparisonRequest) -> ComparisonResult:
    if request.action == "list":
        return ComparisonCatalog(models=(MODEL,))
    if request.model_id != MODEL.id or request.model_version != MODEL.version:
        raise ValueError("Comparison model changed; choose its current declaration")
    if request.action in ("inspect", "fit"):
        selected = comparison_inputs(lab, request, MODEL)
        if request.action == "inspect":
            return selected.curves
        if set(request.parameters) - {"offset_ghz"}:
            raise ValueError("Unknown signal model parameter")
        offset = request.parameters.get("offset_ghz", 0.0)
        if not -0.1 <= offset <= 0.1:
            raise ValueError("Carrier offset must be between -0.1 and 0.1 GHz")
        assert request.primary is not None and request.secondary is not None
        fit = selected.context.trace(
            "fit",
            fn=fit_signal,
            inputs={
                "primary": selected.primary,
                "secondary": selected.secondary,
                "primary_points": request.primary.points,
                "secondary_points": request.secondary.points,
                "primary_run": request.primary_run,
                "secondary_run": request.secondary_run,
                "offset_ghz": offset,
            },
        )
        rows: list[_Observation] = [
            {
                "frequency": Quantity(x, curve.coordinate_unit).to("GHz").value,
                "response": Quantity(y, curve.observable_unit).to("V").value,
                "run": curve.run_id,
                "point": point,
            }
            for curve, points in (
                (selected.curves.primary, request.primary.points),
                (selected.curves.secondary, request.secondary.points),
            )
            for x, y, point in zip(curve.x, curve.y, points, strict=True)
        ]
        grid = np.linspace(
            min(row["frequency"] for row in rows),
            max(row["frequency"] for row in rows),
            80,
        )
        fields = {
            "frequency": AnalysisField(role="coordinate", unit="GHz"),
            "response": AnalysisField(role="observable", unit="V"),
        }
        analysis = (
            selected.context.result(
                f"Signal fit · {MODEL.version} · offset {offset:g} GHz"
            )
            .fact("fit", fit, schema=FIT_SCHEMA)
            .fact(
                "next-input",
                SignalNextInput(
                    CONTROL_ENTRY.id,
                    CONTROL_ENTRY.version,
                    Quantity(fit.center_ghz, "GHz"),
                ),
                schema=NEXT_INPUT_SCHEMA,
            )
            .dataset("selected", pd.DataFrame(rows), fields=fields)
            .dataset(
                "curve",
                pd.DataFrame(
                    {
                        "frequency": grid,
                        "response": np.polynomial.polynomial.polyval(
                            grid, fit.coefficients
                        ),
                    }
                ),
                fields=fields,
            )
            .figure_layers(
                title="Selected retained signals and quadratic model",
                layers=(
                    AnalysisFigureLayerSpec(
                        id="selected",
                        source=AnalysisDatasetViewSource(output_id="selected"),
                        projection=AnalysisFigureProjection(
                            kind="scatter", x="frequency", y="response", series="run"
                        ),
                    ),
                    AnalysisFigureLayerSpec(
                        id="model",
                        source=AnalysisDatasetViewSource(output_id="curve"),
                        projection=AnalysisFigureProjection(
                            kind="line", x="frequency", y="response"
                        ),
                    ),
                ),
            )
        )
        return _publication(request.primary_run, save_comparison(analysis, request))
    run = lab.get_run(request.primary_run)
    source = run.published_analysis(request.analysis_id)
    if source.publication_hash != request.analysis_hash:
        raise ValueError("Saved analysis does not match its exact publication hash")
    if request.action == "reject":
        proposals = source.parameter_proposals
        if not proposals or not request.reason.strip():
            raise ValueError("Reject requires an exact candidate and a review reason")
        context = run.analysis(
            "Independent candidate rejection", key="comparison-review"
        )
        context.analysis_fact(source, "fit", schema=FIT_SCHEMA)
        review = CandidateReview(
            False,
            run.id,
            source.id,
            source.publication_hash,
            tuple(item.id for item in proposals),
            request.actor,
            request.reason,
        )
        return _publication(
            run.id,
            save_comparison(
                context.result().fact("review", review, schema=REVIEW_SCHEMA), request
            ),
        )
    fit = source.fact_as("fit", FIT_SCHEMA)
    if fit.model_id != MODEL.id or fit.model_version != MODEL.version:
        raise ValueError("Saved result belongs to another model version")
    if request.action == "handoff":
        suggestion = source.fact_as("next-input", NEXT_INPUT_SCHEMA)
        return ComparisonHandoff(
            request=LaunchRequest(
                action="preview",
                experiment=suggestion.experiment,
                version=suggestion.version,
                actor=request.actor,
                control_edits={
                    "frequency": ControlEdit(mode="fixed", value=suggestion.frequency)
                },
            ),
            source_run=run.id,
            source_analysis=source.id,
            source_hash=source.publication_hash,
        )
    context = run.analysis("Independent carrier candidate", key="comparison-candidate")
    retained_fit = context.analysis_fact(source, "fit", schema=FIT_SCHEMA)
    suggestion = context.analysis_fact(source, "next-input", schema=NEXT_INPUT_SCHEMA)
    analysis = (
        context.result()
        .fact("fit", retained_fit, schema=FIT_SCHEMA)
        .fact("next-input", suggestion, schema=NEXT_INPUT_SCHEMA)
        .propose(
            "carrier",
            Q0[DRIVE_CARRIER_FREQUENCY].update(Quantity(fit.center_ghz, "GHz")),
            reason=(
                "Explicit candidate from the retained signal comparison; "
                "no default activation"
            ),
            evidence=("fit",),
        )
    )
    return _publication(run.id, save_comparison(analysis, request))
