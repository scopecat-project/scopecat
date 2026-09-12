from __future__ import annotations

import pytest
from scopecat_testkit.authoring import bind_invocation, load_config

import scopecat as sc
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.measurements.projection import select_measurement_projection
from scopecat.planning.measurement_projection import project_measurement_catalog
from scopecat.planning.point_materialization import prepare_bound_points
from scopecat.records.measurement import (
    MeasurementPointCloudPointDomain,
    MeasurementPointDomainColumn,
    MeasurementVariable,
)
from scopecat.records.run_request import PointCloudDomainRecord

_INT = sc.ScalarType(sc.IntType())
_FREQUENCY = sc.ScalarType(sc.QuantityType(unit="GHz"))


def test_point_rows_compile_materialize_and_persist_layout() -> None:
    x = sc.coordinate("x", _INT)
    y = sc.coordinate("y", _INT)

    @sc.experiment(id="test.point-rows", kind="point_rows")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.points(
            (
                {x: 1, y: 10},
                {x: 1, y: 10},
                {x: 3, y: 30},
            )
        )
        experiment.alias(x, record_id="observed_x")

    invocation = experiment()
    compiled = compile_invocation(invocation)

    assert compiled.program.program.point_domain_layout == "point_cloud"
    request_points = compiled.request.point_plan.domain
    assert isinstance(request_points, PointCloudDomainRecord)
    assert request_points.columns == ["x", "y"]
    assert request_points.rows == [
        {"x": 1, "y": 10},
        {"x": 1, "y": 10},
        {"x": 3, "y": 30},
    ]

    bound = bind_invocation(invocation, config_profile=load_config())
    bound_points = prepare_bound_points(bound)
    domain = bound_points.point_domain
    assert domain.layout == "point_cloud"
    assert domain.axis_sizes == (("x", 3), ("y", 3))
    assert [point.row for point in domain.points] == request_points.rows
    assert [point.logical_ordinal for point in domain.points] == [0, 1, 2]
    assert domain.points[0].logical_id != domain.points[1].logical_id

    catalog = project_measurement_catalog(bound_points)
    projection = select_measurement_projection(
        catalog,
        bound.bindings.record_uses,
    )
    schema = projection.schema
    assert schema is not None
    assert schema.point_domain == MeasurementPointCloudPointDomain(
        columns=[
            MeasurementPointDomainColumn(id="x"),
            MeasurementPointDomainColumn(id="y"),
        ]
    )
    assert schema.metadata == {"experiment_id": "test.point-rows"}


def test_empty_point_rows_are_a_zero_point_domain() -> None:
    x = sc.coordinate("x", _INT)
    y = sc.coordinate("y", _FREQUENCY)

    @sc.experiment(id="test.empty-point-rows", kind="point_rows")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.points((), coordinates=(x, y))
        experiment.alias(x, record_id="observed_x")

    invocation = experiment()
    compiled = compile_invocation(invocation)
    request_points = compiled.request.point_plan.domain
    assert isinstance(request_points, PointCloudDomainRecord)
    assert request_points.columns == ["x", "y"]
    assert request_points.rows == []

    bound = bind_invocation(invocation, config_profile=load_config())
    bound_points = prepare_bound_points(bound)
    domain = bound_points.point_domain
    assert domain.layout == "point_cloud"
    assert tuple(domain.points) == ()
    assert bound.point_domain.cardinality == 0

    catalog = project_measurement_catalog(bound_points)
    projection = select_measurement_projection(
        catalog,
        bound.bindings.record_uses,
    )
    schema = projection.schema
    assert schema is not None
    assert schema.point_domain == MeasurementPointCloudPointDomain(
        columns=[
            MeasurementPointDomainColumn(id="x"),
            MeasurementPointDomainColumn(id="y"),
        ]
    )
    assert schema.variables[:2] == (
        MeasurementVariable(
            id="x",
            role="coordinate",
            dtype="int64",
            dims=["point"],
        ),
        MeasurementVariable(
            id="y",
            role="coordinate",
            dtype="float64",
            unit="GHz",
            dims=["point"],
        ),
    )


def test_point_rows_require_the_same_typed_coordinate_columns() -> None:
    x = sc.coordinate("x", _INT)
    y = sc.coordinate("y", _INT)

    with pytest.raises(ValueError, match="same typed coordinate columns"):
        sc.ExperimentContext().points(({x: 1, y: 2}, {x: 3}))


def test_point_rows_cannot_be_combined_with_grid_scans() -> None:
    x = sc.coordinate("x", _INT)

    def definition(experiment: sc.ExperimentContext) -> None:
        experiment.grid(sc.axis(x, (1, 2)))
        experiment.points(({x: 3},))

    with pytest.raises(ValueError, match="can only be declared once"):
        sc.experiment(id="test.mixed-point-domain", kind="point_rows")(definition)()
