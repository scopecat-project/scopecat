"""Point-domain scan intents shared by authoring and compilation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, cast

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Int, Scalar
from scopecat.kernel.value_validation import validate_literal
from scopecat.program.expressions import ParameterLookupUse
from scopecat.program.input_capture import capture_runtime_input
from scopecat.program.parameters import (
    ParameterContract,
    merge_parameter_contracts,
)
from scopecat.program.value_refs import (
    CoordinateRef,
    ScalarOperand,
    ValueRef,
    internal_coordinate_ref_id,
    internal_value_ref_parameter_contracts,
    internal_value_ref_parameter_lookup,
)

type ScanValue = Quantity | EntityRef | str | int | float | bool | None
type ScanCenter = ValueRef | Quantity
type ScanRangeValue = Quantity | int | float
type PointRow = Mapping[CoordinateRef, ScanValue]
type RepeatMode = Literal["point", "sweep"]
type PointTraversal = Literal["forward", "snake"]
type PointGroupScheduling = Literal["prefer_together"]
type PointGroupInterruption = Literal["restart_group"]

_REPEAT_AXIS_ID = "repeat"


@dataclass(frozen=True, slots=True)
class ValuesScanSource:
    values: tuple[ScanValue, ...]


@dataclass(frozen=True, slots=True)
class AroundScanSource:
    """A fixed-count linear axis around one explicit center."""

    center: ScanCenter
    span: Quantity
    points: int


@dataclass(frozen=True, slots=True)
class RangeScanSource:
    """A fixed-count linear axis between two literal coordinate endpoints."""

    start: ScanRangeValue
    stop: ScanRangeValue
    points: int


type ScanSource = ValuesScanSource | AroundScanSource | RangeScanSource


@dataclass(frozen=True, slots=True, repr=False)
class AxisSpec:
    id: str
    value_type: Scalar
    source: ScanSource
    overlay: ValueRef | None = None
    mode: Literal["fixed", "scan"] = "scan"


@dataclass(frozen=True, slots=True, repr=False)
class GridSpec:
    """One declaration-ordered Cartesian point domain."""

    axes: tuple[AxisSpec, ...] = ()

    def __post_init__(self) -> None:
        _require_unique_axis_ids(self.axes, context="grid")


@dataclass(frozen=True, slots=True, repr=False)
class PointsSpec:
    """One ordered point cloud represented by equal-length coordinate columns."""

    axes: tuple[AxisSpec, ...] = ()

    def __post_init__(self) -> None:
        _require_unique_axis_ids(self.axes, context="points")
        sources = tuple(axis.source for axis in self.axes)
        if any(not isinstance(source, ValuesScanSource) for source in sources):
            raise TypeError("point-cloud columns require explicit values")
        lengths = {len(cast("ValuesScanSource", source).values) for source in sources}
        if len(lengths) > 1:
            raise ValueError("point-cloud columns must have equal lengths")


type PointDomainSpec = GridSpec | PointsSpec


@dataclass(frozen=True, slots=True)
class PointGrouping:
    """A named point partition used for scheduling preference and recovery.

    Coordinates listed in ``varying_coordinate_ids`` may vary within one
    group; all remaining coordinates form the group key. Scheduling keeps
    equal-key rows adjacent when practical, but hardware batching may split a
    group. Durable coverage advances only after the whole group completes, so
    interruption restarts that group.
    """

    id: str
    varying_coordinate_ids: tuple[str, ...]
    scheduling: PointGroupScheduling = "prefer_together"
    on_interruption: PointGroupInterruption = "restart_group"

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("point grouping id must be non-empty")
        if any(not coordinate_id for coordinate_id in self.varying_coordinate_ids):
            raise ValueError("point grouping coordinate ids must be non-empty")
        if len(self.varying_coordinate_ids) != len(set(self.varying_coordinate_ids)):
            raise ValueError("point grouping coordinate ids must be unique")
        if self.scheduling != "prefer_together":
            raise ValueError("point grouping scheduling must be 'prefer_together'")
        if self.on_interruption != "restart_group":
            raise ValueError(
                "point grouping interruption policy must be 'restart_group'"
            )


@dataclass(frozen=True, slots=True)
class PointSchedule:
    """Preferred physical point order and its related-point partition.

    ``traversal`` defines the base path. When ``grouping`` is present, the
    resolved order follows groups by their first appearance on that path and
    preserves the path order of members inside each group. Hardware batching
    may still split that preferred order at any point boundary. The grouping's
    interruption policy is consumed separately by durable recovery.
    """

    traversal: PointTraversal = "forward"
    grouping: PointGrouping | None = None

    def __post_init__(self) -> None:
        if self.traversal not in ("forward", "snake"):
            raise ValueError("point traversal must be 'forward' or 'snake'")


@dataclass(frozen=True, slots=True)
class PointPlan:
    """One complete logical point plan for an experiment invocation.

    ``domain`` is either a Cartesian grid or explicit ordered point rows.
    ``repeat`` adds the canonical ``repeat`` coordinate: ``point`` mode keeps
    repeats of each base point adjacent, while ``sweep`` mode keeps complete
    sweeps adjacent. ``schedule.traversal`` may change physical grid execution
    order, but logical point identities and result order remain canonical.
    Explicit point rows therefore support only forward traversal. ``schedule`` combines
    base traversal with a coordinate-defined comparison or recovery grouping.
    It influences preferred order and durable restart boundaries without
    imposing a hardware batch shape.
    """

    domain: PointDomainSpec = field(default_factory=GridSpec)
    repeat: int = 1
    repeat_mode: RepeatMode = "point"
    schedule: PointSchedule = field(default_factory=PointSchedule)

    def __post_init__(self) -> None:
        if type(self.repeat) is not int or self.repeat <= 0:
            raise ValueError("point repeat must be a positive integer")
        if self.repeat_mode not in ("point", "sweep"):
            raise ValueError("point repeat mode must be 'point' or 'sweep'")
        if isinstance(self.domain, PointsSpec) and self.schedule.traversal == "snake":
            raise ValueError("point clouds only support forward traversal")
        if self.repeat > 1 and any(
            axis.id == _REPEAT_AXIS_ID for axis in self.domain.axes
        ):
            raise ValueError("repeated point plans reserve the 'repeat' axis id")


def expand_point_plan(plan: PointPlan) -> PointDomainSpec:
    """Expand repeat policy into one canonical point-domain declaration."""

    if plan.repeat == 1:
        return plan.domain
    if isinstance(plan.domain, GridSpec):
        repeat_axis = _repeat_axis(
            tuple(range(plan.repeat)),
            maximum=plan.repeat - 1,
        )
        axes = (
            (*plan.domain.axes, repeat_axis)
            if plan.repeat_mode == "point"
            else (repeat_axis, *plan.domain.axes)
        )
        return GridSpec(axes)
    return _expand_point_cloud(plan.domain, plan.repeat, mode=plan.repeat_mode)


def _expand_point_cloud(
    points: PointsSpec,
    repeat: int,
    *,
    mode: RepeatMode,
) -> PointsSpec:
    sources = tuple(cast("ValuesScanSource", axis.source) for axis in points.axes)
    row_count = len(sources[0].values) if sources else 0
    if mode == "point":
        expanded_axes = tuple(
            replace(
                axis,
                source=ValuesScanSource(
                    tuple(value for value in source.values for _index in range(repeat))
                ),
            )
            for axis, source in zip(points.axes, sources, strict=True)
        )
        repeat_values = tuple(
            repeat_index
            for _row_index in range(row_count)
            for repeat_index in range(repeat)
        )
        return PointsSpec(
            (
                *expanded_axes,
                _repeat_axis(repeat_values, maximum=repeat - 1),
            )
        )
    expanded_axes = tuple(
        replace(
            axis,
            source=ValuesScanSource(source.values * repeat),
        )
        for axis, source in zip(points.axes, sources, strict=True)
    )
    repeat_values = tuple(
        repeat_index
        for repeat_index in range(repeat)
        for _row_index in range(row_count)
    )
    return PointsSpec(
        (
            _repeat_axis(repeat_values, maximum=repeat - 1),
            *expanded_axes,
        )
    )


def _repeat_axis(values: tuple[int, ...], *, maximum: int) -> AxisSpec:
    return AxisSpec(
        id=_REPEAT_AXIS_ID,
        value_type=Scalar(Int(minimum=0, maximum=maximum)),
        source=ValuesScanSource(values),
    )


def points_spec(
    rows: Sequence[PointRow],
    *,
    coordinates: Sequence[CoordinateRef] = (),
) -> PointsSpec:
    """Capture ordered point rows as equal-length typed coordinate columns."""

    selected_rows = tuple(rows)
    selected_coordinates = tuple(coordinates)
    if not selected_coordinates and selected_rows:
        selected_coordinates = tuple(selected_rows[0])
    if selected_rows and not selected_coordinates:
        raise ValueError("non-empty points require at least one coordinate column")
    coordinate_specs = tuple(
        _point_coordinate(coordinate) for coordinate in selected_coordinates
    )
    _require_unique_ids(
        tuple(axis_id for _coordinate, axis_id, _value_type in coordinate_specs),
        context="points",
    )
    expected = frozenset(selected_coordinates)
    for row in selected_rows:
        if frozenset(row) != expected:
            raise ValueError(
                "points rows must contain the same typed coordinate columns"
            )
    return PointsSpec(
        tuple(
            AxisSpec(
                id=axis_id,
                value_type=value_type,
                source=ValuesScanSource(
                    tuple(
                        _capture_point_value(
                            value_type,
                            axis_id,
                            row[coordinate],
                            row_index=row_index,
                        )
                        for row_index, row in enumerate(selected_rows)
                    )
                ),
            )
            for coordinate, axis_id, value_type in coordinate_specs
        )
    )


def _point_coordinate(
    coordinate: CoordinateRef,
) -> tuple[CoordinateRef, str, Scalar]:
    return (
        coordinate,
        internal_coordinate_ref_id(coordinate),
        coordinate.value_type,
    )


def _capture_point_value(
    value_type: Scalar,
    coordinate_id: str,
    value: ScanValue,
    *,
    row_index: int,
) -> ScanValue:
    validate_literal(
        value_type,
        value,
        path=("points", "rows", row_index, coordinate_id),
    )
    return cast("ScanValue", capture_runtime_input(value))


def _require_unique_axis_ids(
    axes: Sequence[AxisSpec],
    *,
    context: str,
) -> None:
    _require_unique_ids(tuple(axis.id for axis in axes), context=context)


def _require_unique_ids(ids: Sequence[str], *, context: str) -> None:
    if any(not axis_id for axis_id in ids):
        raise ValueError(f"{context} axis ids must be non-empty")
    if len(ids) != len(set(ids)):
        raise ValueError(f"{context} axis ids must be unique")


def parameter_overlay_cell(
    axis: AxisSpec,
) -> tuple[
    ParameterLookupUse,
    tuple[tuple[str, ScalarOperand], ...],
]:
    if axis.overlay is None:
        raise TypeError("scan axis does not overlay a parameter cell")
    lookup = internal_value_ref_parameter_lookup(axis.overlay)
    assert lookup is not None
    return lookup


def axis_parameter_contracts(axis: AxisSpec) -> tuple[ParameterContract, ...]:
    return merge_parameter_contracts(
        _value_parameter_contracts(axis.overlay),
        _source_parameter_contracts(axis),
    )


def _source_parameter_contracts(axis: AxisSpec) -> tuple[ParameterContract, ...]:
    source = axis.source
    if not isinstance(source, AroundScanSource):
        return ()
    return _value_parameter_contracts(source.center)


def _value_parameter_contracts(value: object) -> tuple[ParameterContract, ...]:
    return (
        internal_value_ref_parameter_contracts(value)
        if isinstance(value, ValueRef)
        else ()
    )
