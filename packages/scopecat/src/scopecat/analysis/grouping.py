"""Explicit offline groups over retained points; no implicit scientific averaging."""

from __future__ import annotations

from dataclasses import dataclass

from scopecat.analysis.arguments import AnalysisArgument
from scopecat.kernel.quantity import Quantity
from scopecat.measurements.dataset import Dataset
from scopecat.records.analysis_grouping import AnalysisGrouping


@dataclass(frozen=True)
class DatasetGroup:
    coordinates: dict[str, AnalysisArgument]
    positions: tuple[int, ...]


def partition_groups(
    data: Dataset, grouping: AnalysisGrouping
) -> tuple[DatasetGroup, ...]:
    if grouping.fitting not in data.coords:
        raise ValueError(f"unknown fitting coordinate {grouping.fitting!r}")
    by = list(grouping.by)
    if grouping.repeats == "separate":
        by.extend(
            name
            for name in data.coords
            if name.rsplit("/", 1)[-1] == "repeat" and name not in by
        )
    groups: list[tuple[dict[str, AnalysisArgument], Dataset]] = [({}, data)]
    for name in by:
        if name not in data.coords:
            raise ValueError(f"unknown grouping coordinate {name!r}")
        data[name].require_point_scalar()
        _ = data[name].require_values()
        expanded: list[tuple[dict[str, AnalysisArgument], Dataset]] = []
        for coordinates, selected in groups:
            for key, subset in selected.groupby(name).items():
                if not isinstance(key, str | bool | int | float):
                    raise TypeError(
                        f"{name}: grouping requires scalar "
                        "numeric or string coordinates"
                    )
                unit = data[name].unit
                native = Quantity(float(key), unit) if unit else key
                expanded.append(({**coordinates, name: native}, subset))
        groups = expanded
    positions = {point: index for index, point in enumerate(data.point_indices)}
    result = tuple(
        DatasetGroup(coordinates, tuple(positions[p] for p in subset.point_indices))
        for coordinates, subset in groups
    )
    if sum(len(group.positions) for group in result) != len(data):
        raise ValueError(
            "group coordinates must cover every point without missing values"
        )
    return result
