# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
"""Bounded, unit-aware figure projection; statistics remain project-owned."""

from __future__ import annotations

from collections.abc import Sequence

from scopecat.analysis.datasets import DerivedDataset
from scopecat.kernel.units import compatible_units, convert_linear_value
from scopecat.records.analysis import (
    MAX_ANALYSIS_FIGURE_POINTS,
    AnalysisFigure,
    AnalysisFigureAxis,
    AnalysisFigureLayerSpec,
    AnalysisFigureLayerView,
    AnalysisFigureSeries,
    AnalysisFigureView,
)


def project_figure_layers(
    layers: Sequence[tuple[AnalysisFigureLayerSpec, DerivedDataset, int]],
) -> AnalysisFigureView:
    """Allocate the shared budget before any Arrow-to-Python materialization."""
    count = len(layers)
    axes: tuple[AnalysisFigureAxis, AnalysisFigureAxis] | None = None
    projected: list[AnalysisFigureLayerView] = []
    for index, (layer, dataset, total_points) in enumerate(layers):
        projection = layer.projection
        fields = {field.name: field for field in dataset.schema.fields}
        x_field, y_field = fields[projection.x], fields[projection.y]
        if axes is None:
            axes = (
                AnalysisFigureAxis(
                    label=x_field.label or x_field.name, unit=x_field.unit
                ),
                AnalysisFigureAxis(
                    label=y_field.label or y_field.name, unit=y_field.unit
                ),
            )
        x_factor = _factor(x_field.unit, axes[0].unit)
        y_factor = _factor(y_field.unit, axes[1].unit)
        names = [projection.x, projection.y]
        if projection.series is not None:
            names.append(projection.series)
        uncertainty = projection.uncertainty
        lower_factor = upper_factor = 1.0
        if uncertainty is not None:
            names.extend((uncertainty.lower, uncertainty.upper))
            lower_factor = _factor(fields[uncertainty.lower].unit, axes[1].unit)
            upper_factor = _factor(fields[uncertainty.upper].unit, axes[1].unit)
        selected = dataset.table.select(tuple(dict.fromkeys(names))).slice(
            0, figure_layer_budget(count, index)
        )
        groups: dict[str, list[dict[str, object]]] = {}
        for row in selected.to_pylist():
            group = (
                str(row[projection.series])
                if projection.series
                else projection.label or projection.y
            )
            groups.setdefault(group, []).append(row)
        series = [
            AnalysisFigureSeries(
                id=group,
                label=group,
                x=[_number(row[projection.x]) * x_factor for row in rows],
                y=[_number(row[projection.y]) * y_factor for row in rows],
                y_lower=None
                if uncertainty is None
                else [_number(row[uncertainty.lower]) * lower_factor for row in rows],
                y_upper=None
                if uncertainty is None
                else [_number(row[uncertainty.upper]) * upper_factor for row in rows],
            )
            for group, rows in groups.items()
        ]
        projected.append(
            AnalysisFigureLayerView(
                id=layer.id,
                source=layer.source,
                projection=layer.projection,
                preview=AnalysisFigure(
                    kind=projection.kind, x_axis=axes[0], y_axis=axes[1], series=series
                ),
                total_points=total_points,
                truncated=len(selected) < total_points,
            )
        )
    return AnalysisFigureView(
        layers=tuple(projected),
        total_points=sum(layer.total_points for layer in projected),
        truncated=any(layer.truncated for layer in projected),
    )


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("analysis figure values must be real numbers")
    return float(value)


def _factor(source: str | None, target: str | None) -> float:
    if source == target:
        return 1.0
    if source is not None and target is not None and compatible_units(source, target):
        converted = convert_linear_value(1.0, source, target)
        if converted is not None:
            return converted
    raise ValueError(f"figure axes require compatible units: {source!r} and {target!r}")


def figure_layer_budget(layer_count: int, index: int) -> int:
    """Equal deterministic shares; the first remainder layers receive one more."""
    budget, remainder = divmod(MAX_ANALYSIS_FIGURE_POINTS, layer_count)
    return budget + int(index < remainder)
