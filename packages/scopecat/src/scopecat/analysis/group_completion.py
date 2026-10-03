"""Complete group identities from the recorded point-domain contract."""

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import islice, product
from math import prod

from scopecat.analysis.arguments import AnalysisArgument
from scopecat.kernel.quantity import Quantity
from scopecat.records.analysis_grouping import AnalysisGrouping
from scopecat.records.measurement import (
    MeasurementDatasetSchema,
    MeasurementProductGridPointDomain,
    iter_measurement_point_axis_values,
)


@dataclass(frozen=True)
class PlannedAnalysisGroup:
    coordinates: dict[str, AnalysisArgument]
    point_indices: tuple[int, ...]


def planned_groups(
    schema: MeasurementDatasetSchema,
    grouping: AnalysisGrouping,
    *,
    max_groups: int,
    max_points: int,
    offset: int = 0,
    limit: int | None = None,
) -> Iterator[PlannedAnalysisGroup]:
    """Enumerate bounded groups without reading acquisition or provisional values.

    Only a closed product grid proves future membership before acquisition ends.
    Point clouds and adaptive runs need a separate explicit completion contract.
    Duplicate coordinate values belong to the same group, as in offline grouping.
    """
    domain = schema.point_domain
    if not isinstance(domain, MeasurementProductGridPointDomain):
        raise ValueError("live groups require a fixed product-grid point domain")
    coordinates = {
        variable.id for variable in schema.variables if variable.role == "coordinate"
    }
    if grouping.fitting not in coordinates:
        raise ValueError(f"unknown fitting coordinate {grouping.fitting!r}")
    by = list(grouping.by)
    if grouping.repeats == "separate":
        by.extend(
            axis.id
            for axis in domain.axes
            if axis.id.rsplit("/", 1)[-1] == "repeat" and axis.id not in by
        )
    axes = {axis.id: axis for axis in domain.axes}
    if missing := set(by) - axes.keys():
        raise ValueError(
            f"live group coordinates must be planned point axes: {sorted(missing)}"
        )
    choices: list[list[tuple[AnalysisArgument, tuple[int, ...]]]] = []
    for name in by:
        axis = axes[name]
        indices: dict[str | bool | int | float, list[int]] = {}
        native: dict[str | bool | int | float, AnalysisArgument] = {}
        for index, value in enumerate(iter_measurement_point_axis_values(axis)):
            if index >= 65536:
                raise ValueError("group coordinate planning exceeds 65536 axis values")
            if value is None or not isinstance(value.value, str | bool | int | float):
                raise ValueError(f"{name}: groups require available scalar coordinates")
            key = value.value
            indices.setdefault(key, []).append(index)
            if len(indices) > max_groups:
                raise ValueError("group count exceeds the configured group budget")
            if len(indices[key]) > max_points:
                raise ValueError("group size exceeds the configured point budget")
            native[key] = Quantity(float(key), value.unit) if value.unit else key
        choices.append(
            [(native[key], tuple(positions)) for key, positions in indices.items()]
        )
    if prod(len(choice) for choice in choices) > max_groups:
        raise ValueError("group count exceeds the configured group budget")
    sizes = tuple(axis.size for axis in domain.axes)
    strides = tuple(prod(sizes[index + 1 :]) for index in range(len(sizes)))
    for selected in islice(
        product(*choices), offset, None if limit is None else offset + limit
    ):
        members = {name: choice[1] for name, choice in zip(by, selected, strict=True)}
        ranges = [members.get(axis.id, range(axis.size)) for axis in domain.axes]
        if prod(len(indices) for indices in ranges) > max_points:
            raise ValueError("group size exceeds the configured point budget")
        points = tuple(
            sum(index * stride for index, stride in zip(indices, strides, strict=True))
            for indices in product(*ranges)
        )
        if points:
            yield PlannedAnalysisGroup(
                coordinates={
                    name: choice[0] for name, choice in zip(by, selected, strict=True)
                },
                point_indices=points,
            )
