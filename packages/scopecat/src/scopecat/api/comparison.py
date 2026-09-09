"""Selection and unit checks shared by lab-owned two-run analysis callbacks."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.kernel.quantity import Quantity
from scopecat.records.analysis import AnalysisFactRecordOutput
from scopecat.records.comparison import (
    ComparisonCurve,
    ComparisonInspection,
    ComparisonModel,
    ComparisonRequest,
    ComparisonSelection,
)

if TYPE_CHECKING:
    from scopecat.api.analysis import Analysis, AnalysisContext
    from scopecat.api.lab import LabClient
    from scopecat.api.published_analysis import PublishedAnalysis
    from scopecat.daemon.views import RunAnalysisView
    from scopecat.measurements.dataset import Dataset


COMPARISON_REQUEST_FACT = "comparison-request"
COMPARISON_REQUEST_SCHEMA = AnalysisFactSchema(
    "scopecat.comparison-request.v1", ComparisonRequest
)


def save_comparison(
    analysis: Analysis, request: ComparisonRequest
) -> PublishedAnalysis:
    """Publish through the existing analysis owner with its exact public request.

    Providers use this for fit, candidate and independent review publications.
    The request records the original model and selection for later source actions.
    """
    return analysis.fact(
        COMPARISON_REQUEST_FACT, request, schema=COMPARISON_REQUEST_SCHEMA
    ).save()


def reopen_comparison(
    request: ComparisonRequest, source: RunAnalysisView
) -> ComparisonRequest:
    """Bind a source action to a retained request before loading project code."""
    if (
        source.run_id != request.primary_run
        or source.entry.id != request.analysis_id
        or source.analysis.publication_hash != request.analysis_hash
    ):
        raise ValueError("Saved comparison does not match its exact run/analysis/hash")
    output = next(
        (
            item
            for item in source.analysis.outputs
            if item.id == COMPARISON_REQUEST_FACT
        ),
        None,
    )
    if not isinstance(output, AnalysisFactRecordOutput):
        raise ValueError("Saved analysis has no public comparison request")
    fact = output.content
    if (
        fact.schema_id != COMPARISON_REQUEST_SCHEMA.id
        or fact.schema_codec != COMPARISON_REQUEST_SCHEMA.schema_codec
        or fact.schema_hash != COMPARISON_REQUEST_SCHEMA.schema_hash
    ):
        raise ValueError("Saved comparison request schema is not supported")
    original = COMPARISON_REQUEST_SCHEMA.decode(fact.value)
    if original.primary_run != source.run_id:
        raise ValueError("Saved comparison request does not belong to its primary run")
    return original.model_copy(
        update={
            "action": request.action,
            "analysis_id": source.entry.id,
            "analysis_hash": source.analysis.publication_hash,
            "actor": request.actor,
            "reason": request.reason,
        }
    )


@dataclass(frozen=True)
class SelectedComparison:
    """Exact independent datasets; normalization does not join their grids."""

    context: AnalysisContext
    primary: Dataset
    secondary: Dataset
    curves: ComparisonInspection


def comparison_inputs(
    lab: LabClient,
    request: ComparisonRequest,
    model: ComparisonModel,
) -> SelectedComparison:
    """Load two completed inputs and check the exact inspected hashes for a fit.

    Scope is one real scalar coordinate and observable per point. Different grids
    are allowed: each selected point retains its source run and position. Arrays,
    complex data, missing values and incompatible units require a lab-specific
    projection before this bounded comparison surface can use them.
    """
    if request.primary_run == request.secondary_run:
        raise ValueError("Choose two distinct retained runs")
    primary_run = lab.get_run(request.primary_run)
    secondary_run = lab.get_run(request.secondary_run)
    if primary_run.status != "completed" or secondary_run.status != "completed":
        raise ValueError("Comparison requires two completed retained runs")
    context = primary_run.analysis("Retained-run comparison", key="comparison")
    primary = context.measurements(id="primary")
    secondary = context.measurements(run=secondary_run, id="secondary")
    selected_primary, selected_secondary = primary, secondary
    if request.action == "fit":
        selected_primary = _selection(primary, request.primary, request.primary_run)
        selected_secondary = _selection(
            secondary, request.secondary, request.secondary_run
        )
    left = comparison_curve(selected_primary, request.primary_run, model)
    right = comparison_curve(
        selected_secondary,
        request.secondary_run,
        model,
        coordinate_unit=left.coordinate_unit,
        observable_unit=left.observable_unit,
        normalize=True,
    )
    return SelectedComparison(
        context, primary, secondary, ComparisonInspection(primary=left, secondary=right)
    )


def _selection(
    dataset: Dataset, selection: ComparisonSelection | None, run_id: str
) -> Dataset:
    if selection is None or selection.run_id != run_id:
        raise ValueError("Fit requires exact selections for both retained runs")
    if dataset.entry.content_hash != selection.content_hash:
        raise ValueError("Retained measurement content changed; inspect it again")
    points = selection.points
    if len(set(points)) != len(points) or any(
        p < 0 or p >= len(dataset) for p in points
    ):
        raise ValueError("Selected point positions must be distinct and in range")
    return dataset.isel(point=list(points))


def comparison_curve(
    dataset: Dataset,
    run_id: str,
    model: ComparisonModel,
    *,
    coordinate_unit: str | None = None,
    observable_unit: str | None = None,
    normalize: bool = False,
) -> ComparisonCurve:
    x = dataset[model.coordinate]
    y = dataset[model.observable]
    if x.role != "coordinate" or y.role != "observable":
        raise ValueError(
            "Model coordinate and observable must match their declared roles"
        )
    if x.dims != ("point",) or y.dims != ("point",):
        raise ValueError("Comparison supports point-local real scalars only")
    if len(dataset) > 10000:
        raise ValueError("Comparison supports at most 10000 retained points per run")
    x_unit = coordinate_unit if normalize else x.unit
    y_unit = observable_unit if normalize else y.unit

    def values(name: str, unit: str | None) -> tuple[float, ...]:
        variable = dataset[name]
        result: list[float] = []
        for value in variable.values:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError("Comparison requires available real scalar values")
            number = float(value)
            if variable.unit != unit:
                if variable.unit is None or unit is None:
                    raise ValueError(
                        "Unitless and unit-bearing variables are incompatible"
                    )
                number = Quantity(number, variable.unit).to(unit).value
            if not math.isfinite(number):
                raise ValueError("Comparison requires finite values")
            result.append(number)
        return tuple(result)

    return ComparisonCurve(
        run_id=run_id,
        content_hash=dataset.entry.content_hash,
        coordinate=model.coordinate,
        observable=model.observable,
        coordinate_unit=x_unit,
        observable_unit=y_unit,
        x=values(model.coordinate, x_unit),
        y=values(model.observable, y_unit),
    )
