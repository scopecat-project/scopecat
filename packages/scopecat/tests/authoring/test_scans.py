from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, assert_type

import numpy as np
import pytest

import scopecat as sc
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.program.scans import GridSpec


def test_experiment_scan_infers_coordinate_shape_without_sampling_bounds() -> None:
    @sc.module
    def bounded_consumer(
        module: sc.ModuleContext,
        count: Annotated[sc.Input[int], sc.IntType()],
        bias: Annotated[sc.Input[sc.Quantity], sc.QuantityType(unit="V")],
    ) -> None:
        del module, count, bias

    @sc.experiment
    def inferred(experiment: sc.ExperimentContext) -> None:
        count: sc.CoordinateRef[int] = experiment.scan("count", (1, 2, 3))
        bias: sc.CoordinateRef[sc.Quantity] = assert_type(
            experiment.scan(
                "bias",
                start=sc.Quantity(-0.25, "V"),
                stop=sc.Quantity(0.25, "V"),
                points=3,
            ),
            sc.CoordinateRef[sc.Quantity],
        )
        experiment.use(bounded_consumer(count=count, bias=bias))

    invocation = inferred()
    plan = invocation.definition.default_point_plan
    assert isinstance(plan.domain, GridSpec)
    count_axis, bias_axis = plan.domain.axes
    assert count_axis.value_type == sc.ScalarType(sc.IntType())
    assert bias_axis.value_type == sc.ScalarType(sc.QuantityType(unit="V"))
    compile_invocation(invocation)


def test_experiment_scan_preserves_explicit_coordinate_admissibility() -> None:
    @sc.experiment
    def constrained(experiment: sc.ExperimentContext) -> None:
        experiment.scan(
            "bias",
            (-0.25, 0.0, 0.25),
            value_type=sc.QuantityType(unit="V", minimum=-1.0, maximum=1.0),
        )

    [axis] = constrained().definition.default_point_plan.domain.axes
    assert axis.value_type == sc.ScalarType(
        sc.QuantityType(unit="V", minimum=-1.0, maximum=1.0)
    )


def test_experiment_scan_materializes_values_once_and_accepts_atom_types() -> None:
    visits = 0

    def entities() -> Iterable[str]:
        nonlocal visits
        visits += 1
        yield "q0"
        yield "q1"

    @sc.experiment
    def inferred(experiment: sc.ExperimentContext) -> None:
        experiment.scan(
            "qubit",
            entities(),
            value_type=sc.EntityType(entity_kind="logical_qubit"),
        )

    assert visits == 0
    [axis] = inferred().definition.default_point_plan.domain.axes
    assert visits == 1
    assert axis.value_type == sc.ScalarType(sc.EntityType(entity_kind="logical_qubit"))


def test_experiment_scan_requires_a_type_for_empty_values() -> None:
    def empty(experiment: sc.ExperimentContext) -> None:
        experiment.scan("empty", ())

    with pytest.raises(TypeError, match="empty scan values require value_type"):
        sc.experiment(empty)()


@pytest.mark.parametrize("scan_first", [False, True])
def test_experiment_scan_cannot_mix_with_explicit_point_domains(
    scan_first: bool,
) -> None:
    coordinate = sc.coordinate("explicit", sc.ScalarType(sc.IntType()))

    def mixed(experiment: sc.ExperimentContext) -> None:
        if scan_first:
            experiment.scan("implicit", (1, 2))
            experiment.grid(sc.axis(coordinate, (1, 2)))
        else:
            experiment.grid(sc.axis(coordinate, (1, 2)))
            experiment.scan("implicit", (1, 2))

    with pytest.raises(ValueError, match="cannot be combined"):
        sc.experiment(mixed)()


def test_around_scan_requires_compatible_quantity_dimensions() -> None:
    frequency = sc.coordinate(
        "frequency",
        sc.ScalarType(sc.QuantityType(unit="GHz")),
    )
    duration = sc.coordinate(
        "duration",
        sc.ScalarType(sc.QuantityType(unit="ns")),
    )

    with pytest.raises(TypeError, match="axis point quantity type"):
        sc.axis(frequency, center=duration, span="20 MHz", points=3)
    with pytest.raises(TypeError, match="incompatible"):
        sc.axis(
            frequency,
            center=sc.Quantity(value=5.0, unit="GHz"),
            span="20 ns",
            points=3,
        )


def test_dbm_is_a_valid_generated_scan_coordinate_unit() -> None:
    power = sc.coordinate(
        "power",
        sc.ScalarType(sc.QuantityType(unit="dBm")),
    )

    sc.axis(power, start=-30.0, stop=0.0, unit="dBm", points=7)
    sc.axis(power, center=-20.0, span=6.0, unit="dBm", points=5)


def test_numpy_linspace_values_remain_a_valid_dbm_axis() -> None:
    power = sc.coordinate(
        "power",
        sc.ScalarType(sc.QuantityType(unit="dBm")),
    )

    sc.axis(power, np.linspace(-30.0, 0.0, 7), unit="dBm")


def test_generated_scan_rejects_non_convertible_coordinate_units() -> None:
    power = sc.coordinate(
        "power",
        sc.ScalarType(sc.QuantityType(unit="dBm")),
    )

    with pytest.raises(TypeError, match=r"axis.stop.*compatible with 'dBm'"):
        sc.axis(
            power,
            start=sc.Quantity(-30.0, "dBm"),
            stop=sc.Quantity(1.0, "W"),
            points=3,
        )
    with pytest.raises(TypeError, match=r"axis.span.*compatible with 'dBm'"):
        sc.axis(
            power,
            center=sc.Quantity(-20.0, "dBm"),
            span=sc.Quantity(1.0, "W"),
            points=3,
        )


def test_dynamic_center_must_declare_an_explicit_coordinate_unit() -> None:
    generic_frequency = sc.ScalarType(sc.QuantityType(dimension="frequency"))
    frequency = sc.coordinate("frequency", generic_frequency)

    with pytest.raises(TypeError, match="center must declare a unit"):
        sc.axis(
            frequency,
            center=sc.parameter("center", generic_frequency),
            span=200.0,
            unit="MHz",
            points=3,
        )


def test_generated_scan_rejects_non_durable_float_endpoints() -> None:
    value = sc.coordinate(
        "value",
        sc.ScalarType(sc.FloatType(finite=False)),
    )

    with pytest.raises(ValueError, match="finite"):
        sc.axis(value, start=0.0, stop=float("inf"), points=3)


def test_generated_scan_does_not_ignore_an_empty_unit() -> None:
    power = sc.coordinate(
        "power",
        sc.ScalarType(sc.QuantityType(unit="dBm")),
    )

    with pytest.raises(ValueError, match="unsupported unit"):
        sc.axis(power, start=-30.0, stop=0.0, unit="", points=3)


def test_scan_forms_are_mutually_exclusive_and_complete() -> None:
    power = sc.coordinate(
        "power",
        sc.ScalarType(sc.QuantityType(unit="dBm")),
    )

    with pytest.raises(ValueError, match="exactly one"):
        sc.axis(
            power,
            (-30.0, -20.0),
            unit="dBm",
            start=-30.0,
            stop=0.0,
            points=7,
        )
    with pytest.raises(ValueError, match="mutually exclusive"):
        sc.axis(
            power,
            start=-30.0,
            stop=0.0,
            center=-20.0,
            span=6.0,
            unit="dBm",
            points=7,
        )
    with pytest.raises(ValueError, match="requires start, stop, and points"):
        sc.axis(power, start=-30.0, unit="dBm", points=7)
    with pytest.raises(ValueError, match="at least 2"):
        sc.axis(power, start=-30.0, stop=0.0, unit="dBm", points=1)


def test_quantity_strings_accept_scientific_notation() -> None:
    delay = sc.coordinate(
        "delay",
        sc.ScalarType(sc.QuantityType(unit="s")),
    )

    sc.axis(delay, start="-1e-12 s", stop="1e-12 s", points=3)


def test_scan_capture_requires_finite_durable_values() -> None:
    frequency = sc.coordinate(
        "frequency",
        sc.ScalarType(sc.QuantityType(unit="GHz", finite=False)),
    )

    with pytest.raises(ValueError, match="finite"):
        sc.axis(frequency, [sc.Quantity(value=float("inf"), unit="GHz")])
