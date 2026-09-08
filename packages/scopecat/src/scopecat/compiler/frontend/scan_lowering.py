"""Lower public scan intent into point-domain and durable request values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from scopecat.compiler.frontend.request_values import (
    project_run_request_scalar,
    project_run_request_value,
)
from scopecat.compiler.frontend.value_binding import bind_scalar_input_refs
from scopecat.kernel.value_types import Scalar
from scopecat.program.expressions import ScalarExpr, as_scalar_expr
from scopecat.program.point_domain import (
    PointAxes,
    PointAxis,
    point_axis_linear,
    point_axis_range,
    point_axis_values,
)
from scopecat.program.scans import (
    AroundScanSource,
    AxisSpec,
    PointsSpec,
    RangeScanSource,
    ScanValue,
    ValuesScanSource,
    parameter_overlay_cell,
)
from scopecat.program.value_refs import (
    ValueRef,
    internal_lower_scalar_value_ref,
    internal_value_ref_from_expression,
)
from scopecat.records.run_request import (
    AxisRecord,
    PointCloudDomainRecord,
)


def _lower_scan_axis(
    axis: AxisSpec,
    *,
    inputs: Mapping[str, object] | None = None,
) -> PointAxis[ValueRef]:
    """Build one structural point-domain axis from a scalar scan intent."""

    source = axis.source
    if isinstance(source, ValuesScanSource):
        return point_axis_values(
            axis.id,
            axis.value_type,
            source.values,
        )
    if isinstance(source, RangeScanSource):
        return point_axis_range(
            axis.id,
            axis.value_type,
            source.start,
            source.stop,
            source.points,
        )
    return point_axis_linear(
        axis.id,
        axis.value_type,
        _lower_scan_center_value_ref(axis, inputs=inputs),
        source.span,
        source.points,
    )


def lower_scans_point_domain(
    scans: Sequence[AxisSpec],
    *,
    inputs: Mapping[str, object] | None = None,
) -> PointAxes[ValueRef]:
    """Lower scans to declaration-ordered axes."""

    return tuple(_lower_scan_axis(scan, inputs=inputs) for scan in scans)


def project_axis_record(
    axis: AxisSpec,
    *,
    inputs: Mapping[str, object] | None = None,
) -> AxisRecord:
    """Project one axis into the closed durable request value domain."""

    source = axis.source
    if isinstance(source, ValuesScanSource):
        source_record: dict[str, object] = {
            "kind": "values",
            "values": [
                _request_scalar_value(value, inputs=inputs) for value in source.values
            ],
        }
    elif isinstance(source, RangeScanSource):
        source_record = {
            "kind": "range",
            "start": _request_scalar_value(source.start, inputs=inputs),
            "stop": _request_scalar_value(source.stop, inputs=inputs),
            "points": source.points,
        }
    else:
        source_record = {
            "kind": "around",
            "center": project_run_request_scalar(
                _lower_scan_center(axis, inputs=inputs)
            ),
            "span": _request_scalar_value(source.span, inputs=inputs),
            "points": source.points,
        }
    return AxisRecord.model_validate(
        {
            "axis_id": axis.id,
            "mode": axis.mode,
            "source": source_record,
            "overlay": (
                None
                if axis.overlay is None
                else _parameter_overlay_record(axis, inputs=inputs)
            ),
        }
    )


def project_point_cloud_record(
    points: PointsSpec,
    *,
    inputs: Mapping[str, object] | None = None,
) -> PointCloudDomainRecord:
    """Project a typed point cloud into ordered durable request rows."""

    axes = points.axes
    values_by_axis: tuple[tuple[ScanValue, ...], ...] = tuple(
        cast("ValuesScanSource", axis.source).values for axis in axes
    )
    row_count = len(values_by_axis[0]) if values_by_axis else 0
    return PointCloudDomainRecord.model_validate(
        {
            "columns": [axis.id for axis in axes],
            "rows": [
                {
                    axis.id: _request_scalar_value(
                        values_by_axis[axis_index][row_index],
                        inputs=inputs,
                    )
                    for axis_index, axis in enumerate(axes)
                }
                for row_index in range(row_count)
            ],
        }
    )


def _parameter_overlay_record(
    axis: AxisSpec,
    *,
    inputs: Mapping[str, object] | None,
) -> dict[str, object]:
    lookup, key = parameter_overlay_cell(axis)
    return {
        "kind": "parameter_lookup",
        "table_id": lookup.table_id,
        "key": {
            name: _request_scalar_value(value, inputs=inputs) for name, value in key
        },
        "column": lookup.column_id,
    }


def _lower_scan_center(
    axis: AxisSpec,
    *,
    inputs: Mapping[str, object] | None,
) -> ScalarExpr:
    source = axis.source
    assert isinstance(source, AroundScanSource)
    if isinstance(source.center, ValueRef):
        expression = internal_lower_scalar_value_ref(source.center)
    else:
        expression = as_scalar_expr(source.center, value_type=axis.value_type)
    if inputs is None:
        return expression
    return bind_scalar_input_refs(expression, inputs)


def _lower_scan_center_value_ref(
    axis: AxisSpec,
    *,
    inputs: Mapping[str, object] | None,
) -> ValueRef:
    source = axis.source
    assert isinstance(source, AroundScanSource)
    center_type = (
        cast("Scalar", source.center.value_type)
        if isinstance(source.center, ValueRef)
        else axis.value_type
    )
    return internal_value_ref_from_expression(
        _lower_scan_center(axis, inputs=inputs),
        center_type,
    )


def _request_scalar_value(
    value: object,
    *,
    inputs: Mapping[str, object] | None,
) -> object:
    if isinstance(value, ValueRef):
        expression = internal_lower_scalar_value_ref(value)
        if inputs is not None:
            expression = bind_scalar_input_refs(expression, inputs)
        return project_run_request_scalar(expression)
    if isinstance(value, ScalarExpr):
        expression = value
        if inputs is not None:
            expression = bind_scalar_input_refs(expression, inputs)
        return project_run_request_scalar(expression)
    return project_run_request_value(value, path="scan.value")
