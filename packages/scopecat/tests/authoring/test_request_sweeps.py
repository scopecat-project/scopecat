from typing import Annotated

import pytest

import scopecat as sc
from scopecat.application.request_sweeps import compose_request_sweeps
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.program.scans import PointsSpec, ValuesScanSource


class Device(sc.ParameterModel, table="devices"):
    id: sc.Param[str] = sc.param(key=True)
    frequency: sc.Magnitude[float] = sc.quantity(unit="GHz")


@sc.compute
def combine(x: float, y: float, frequency: sc.Quantity) -> float:
    return x + y + frequency.to("GHz").value


@sc.experiment
def probe(
    ctx: sc.ExperimentContext,
    x: Annotated[sc.Input[float], sc.ControlSpec(scannable=True)] = 1.0,
    y: Annotated[sc.Input[float], sc.ControlSpec(scannable=True)] = 2.0,
) -> sc.DataRef[float]:
    return combine(x, y, sc.parameter_ref(Device.frequency, "a"))


def test_request_sweeps_copy_values_and_parameter_targets() -> None:
    original = probe()
    request = original.sweep(x=[1, 2], y=[3, 4], mode="paired")
    assert original.values["x"] == 1
    assert request.scan_mode == "paired"
    request = request.sweep_parameter(Device.frequency, "a", [5, 6], name="frequency")
    assert request.parameter_sweeps[0].values == (
        sc.Quantity(5, "GHz"),
        sc.Quantity(6, "GHz"),
    )
    copied = request.copy()
    with pytest.raises(TypeError, match="immutable"):
        request.parameter_sweeps[0].key["id"] = "other"  # pyright: ignore[reportIndexIssue]
    assert copied.parameter_sweeps[0].key == {"id": "a"}
    with pytest.raises(ValueError, match="not a scannable"):
        original.sweep(missing=[1, 2])


def test_paired_axes_and_consumed_parameter_overlay() -> None:
    invocation = probe.build()
    invocation = invocation.with_axis(
        sc.axis(sc.coordinate("x", sc.ScalarType(sc.FloatType())), [1, 2])
    )
    sweep = (
        probe()
        .sweep_parameter(Device.frequency, "a", [5, 6], name="frequency")
        .parameter_sweeps
    )
    composed = compose_request_sweeps(invocation, mode="paired", parameters=sweep)
    assert isinstance(composed.point_plan.domain, PointsSpec)
    assert all(
        isinstance(axis.source, ValuesScanSource) and len(axis.source.values) == 2
        for axis in composed.point_plan.domain.axes
    )
    compile_invocation(composed)
    unused = (
        probe()
        .sweep_parameter(Device.frequency, "b", [5, 6], name="frequency")
        .parameter_sweeps
    )
    with pytest.raises(ValueError, match="does not consume"):
        compose_request_sweeps(invocation, mode="cartesian", parameters=unused)
