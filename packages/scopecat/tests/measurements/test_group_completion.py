from math import prod

import pytest

from scopecat.analysis.group_completion import planned_groups
from scopecat.records.analysis_grouping import AnalysisGrouping
from scopecat.records.measurement import (
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementPointCloudPointDomain,
    MeasurementPointDomainAxis,
    MeasurementPointDomainRangeSource,
    MeasurementPointDomainValuesSource,
    MeasurementProductGridPointDomain,
    MeasurementScalar,
    MeasurementVariable,
)


def _axis(name: str, values: tuple[int, ...]) -> MeasurementPointDomainAxis:
    return MeasurementPointDomainAxis(
        id=name,
        size=len(values),
        source=MeasurementPointDomainValuesSource(
            values=tuple(
                MeasurementScalar.create(value=value, dtype="int64") for value in values
            )
        ),
    )


def _schema(*axes: MeasurementPointDomainAxis) -> MeasurementDatasetSchema:
    return MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementProductGridPointDomain(axes=axes),
        dimensions=(
            MeasurementDimension(
                id="point", kind="point", size=prod(a.size for a in axes)
            ),
            MeasurementDimension(id="sample", kind="sample", size=100),
        ),
        variables=(
            *(
                MeasurementVariable(
                    id=a.id, role="coordinate", dtype="int64", dims=("point",)
                )
                for a in axes
            ),
            MeasurementVariable(
                id="frequency",
                role="coordinate",
                dtype="float64",
                dims=("point", "sample"),
            ),
            MeasurementVariable(
                id="signal",
                role="observable",
                dtype="float64",
                dims=("point", "sample"),
            ),
        ),
        primary_coordinates=tuple(a.id for a in axes),
        primary_observables=("signal",),
    )


def test_local_frequency_groups_require_only_their_planned_power_points() -> None:
    schema = _schema(_axis("power", (-30, -20, -10)))
    groups = tuple(
        planned_groups(
            schema,
            AnalysisGrouping(by=("power",), fitting="frequency"),
            max_groups=3,
            max_points=1,
        )
    )
    assert [g.coordinates for g in groups] == [
        {"power": -30},
        {"power": -20},
        {"power": -10},
    ]
    assert [g.point_indices for g in groups] == [(0,), (1,), (2,)]


def test_repeated_t1_keeps_all_delays_and_can_combine_repeats() -> None:
    schema = _schema(_axis("delay", (0, 1, 2)), _axis("repeat", (0, 1)))
    separate = tuple(
        planned_groups(
            schema, AnalysisGrouping(by=(), fitting="delay"), max_groups=2, max_points=3
        )
    )
    assert [g.point_indices for g in separate] == [(0, 2, 4), (1, 3, 5)]
    combined = tuple(
        planned_groups(
            schema,
            AnalysisGrouping(by=(), fitting="delay", repeats="combine"),
            max_groups=1,
            max_points=6,
        )
    )
    assert combined[0].point_indices == tuple(range(6))


def test_duplicate_coordinates_share_one_group_and_pages_keep_identity() -> None:
    schema = _schema(_axis("power", (1, 2, 1)), _axis("delay", (0, 1)))
    grouping = AnalysisGrouping(by=("power",), fitting="delay")
    groups = tuple(planned_groups(schema, grouping, max_groups=2, max_points=4))
    assert [g.point_indices for g in groups] == [(0, 1, 4, 5), (2, 3)]
    assert (
        tuple(
            planned_groups(
                schema, grouping, max_groups=2, max_points=4, offset=1, limit=1
            )
        )
        == groups[1:]
    )


def test_large_compact_axis_fails_budget_without_materializing_it() -> None:
    axis = MeasurementPointDomainAxis(
        id="power",
        size=10**9,
        source=MeasurementPointDomainRangeSource(
            start=MeasurementScalar.create(value=0, dtype="int64"),
            stop=MeasurementScalar.create(value=10**9 - 1, dtype="int64"),
        ),
    )
    with pytest.raises(ValueError, match="group count"):
        tuple(
            planned_groups(
                _schema(axis),
                AnalysisGrouping(by=("power",), fitting="frequency"),
                max_groups=3,
                max_points=1,
            )
        )


def test_group_point_budget_and_open_domains_are_explicit() -> None:
    schema = _schema(_axis("delay", (0, 1, 2)))
    grouping = AnalysisGrouping(by=(), fitting="delay")
    with pytest.raises(ValueError, match="group size"):
        tuple(planned_groups(schema, grouping, max_groups=1, max_points=2))
    cloud = schema.model_copy(
        update={"point_domain": MeasurementPointCloudPointDomain(columns=())}
    )
    with pytest.raises(ValueError, match="fixed product-grid"):
        tuple(planned_groups(cloud, grouping, max_groups=1, max_points=3))
