"""Bounded transport facts do not become another program or point enumerator."""

from scopecat.application.inspection import LaunchInspection
from scopecat.planning.preview_models import (
    ExperimentPreview,
    ExperimentPreviewParameterLookup,
    ExperimentPreviewRecord,
)


def test_inspection_limits_collections_and_roundtrips_structured_dependencies() -> None:
    records = tuple(
        ExperimentPreviewRecord(
            id=f"result-{index}",
            role="observable",
            recording_group_id=None,
            unit=None,
            dtype="float64",
            dims=(),
            shape=(),
        )
        for index in range(300)
    )
    preview = ExperimentPreview(
        experiment_id="bounded",
        experiment_kind="experiment",
        schema=None,
        coordinate_ids=(),
        total_point_count=None,
        initial_point_count=0,
        point_limit=10000,
        points=(),
        points_truncated=False,
        records=records,
        parameters=(
            ExperimentPreviewParameterLookup(
                table_id="drive.table",
                column_id="frequency.column",
                key_columns=("qubit",),
            ),
        ),
    )
    facts = LaunchInspection.from_preview(preview)
    assert facts.total_point_count is None
    assert facts.point_limit == 10000
    assert len(facts.records) == facts.item_limit == 256
    assert facts.item_counts["records"] == 300
    assert facts.truncated == ("records",)
    reopened = LaunchInspection.model_validate_json(facts.model_dump_json())
    assert reopened == facts
    dependency = reopened.parameters[0]
    assert dependency.kind == "lookup"
    assert dependency.table_id == "drive.table"
    assert dependency.column_id == "frequency.column"
    assert dependency.key_columns == ("qubit",)
