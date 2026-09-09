"""Concrete compatibility boundaries of the single-coordinate comparison surface."""

from __future__ import annotations

import pytest

from scopecat.api.comparison import comparison_curve
from scopecat.measurements.dataset import Dataset
from scopecat.records.comparison import ComparisonModel
from scopecat.records.content import ContentEntry
from scopecat.records.measurement import (
    MeasurementDataset,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementPointCloudPointDomain,
    MeasurementPointDomainColumn,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementVariable,
)

MODEL = ComparisonModel(
    id="fit",
    version="1",
    title="Fit",
    description="Test",
    coordinate="frequency",
    observable="response",
)


def dataset(
    unit: str, values: tuple[float, ...], *, response_unit: str = "V"
) -> Dataset:
    schema = MeasurementDatasetSchema(
        dataset_id="measurements",
        point_domain=MeasurementPointCloudPointDomain(
            columns=(MeasurementPointDomainColumn(id="frequency"),)
        ),
        dimensions=[MeasurementDimension(id="point", kind="point", size=len(values))],
        variables=[
            MeasurementVariable(
                id="frequency",
                role="coordinate",
                dtype="float64",
                unit=unit,
                dims=["point"],
            ),
            MeasurementVariable(
                id="response",
                role="observable",
                dtype="float64",
                unit=response_unit,
                dims=["point"],
            ),
        ],
        primary_coordinates=["frequency"],
        primary_observables=["response"],
    )
    records = [
        MeasurementRecord(
            run_id="retained",
            point_index=index,
            coordinates={
                "frequency": MeasurementScalar.create(
                    value=value, dtype="float64", unit=unit
                )
            },
            observables={
                "response": MeasurementScalar.create(
                    value=0.1, dtype="float64", unit=response_unit
                )
            },
        )
        for index, value in enumerate(values)
    ]
    return Dataset(
        MeasurementDataset(dataset_schema=schema, records=records),
        ContentEntry(
            role="dataset",
            id="measurements",
            kind="measurement_dataset",
            content_hash="sha256:retained",
            schema=schema.model_dump(mode="json"),
        ),
    )


def test_independent_grid_normalizes_units_without_joining_or_mutating() -> None:
    original = dataset("MHz", (4700.0, 4800.0, 4950.0))
    curve = comparison_curve(
        original.isel(point=[2, 0]),
        "secondary",
        MODEL,
        coordinate_unit="GHz",
        observable_unit="V",
        normalize=True,
    )
    assert curve.x == (4.95, 4.7)
    assert curve.run_id == "secondary"
    assert curve.content_hash == original.entry.content_hash
    assert original["frequency"].require_values() == (4700.0, 4800.0, 4950.0)


@pytest.mark.parametrize("unit,response_unit", [("ns", "V"), ("GHz", "K")])
def test_incompatible_units_are_rejected(unit: str, response_unit: str) -> None:
    with pytest.raises(ValueError, match="cannot convert"):
        comparison_curve(
            dataset(unit, (1.0, 2.0), response_unit=response_unit),
            "secondary",
            MODEL,
            coordinate_unit="GHz",
            observable_unit="V",
            normalize=True,
        )


def test_missing_coordinate_does_not_select_another_column() -> None:
    with pytest.raises(KeyError, match="missing"):
        comparison_curve(
            dataset("GHz", (4.7, 4.8)),
            "primary",
            MODEL.model_copy(update={"coordinate": "missing"}),
        )


def test_reopen_uses_public_frozen_request_and_checks_exact_owner() -> None:
    from datetime import UTC, datetime

    from scopecat.api.comparison import (
        COMPARISON_REQUEST_SCHEMA,
        reopen_comparison,
    )
    from scopecat.daemon.views import RunAnalysisView
    from scopecat.records.analysis import (
        AnalysisFact,
        AnalysisFactRecordOutput,
        AnalysisRecord,
        RunAnalysisSubject,
    )
    from scopecat.records.author_revision import AuthorRevisionRef
    from scopecat.records.comparison import ComparisonRequest, ComparisonSelection

    original = ComparisonRequest(
        action="fit",
        model_id="original",
        code_revision=AuthorRevisionRef(content_hash="sha256:" + "a" * 64),
        model_version="1",
        primary_run="left",
        secondary_run="right",
        parameters={"offset": 0.01},
        primary=ComparisonSelection(
            run_id="left", content_hash="left-hash", points=(3, 1)
        ),
        secondary=ComparisonSelection(
            run_id="right", content_hash="right-hash", points=(2, 0)
        ),
    )
    source = RunAnalysisView(
        run_id="left",
        entry=ContentEntry(
            role="record", kind="analysis", id="fit", content_hash="record-hash"
        ),
        published_at=datetime.now(UTC),
        analysis=AnalysisRecord(
            subject=RunAnalysisSubject(run_id="left"),
            title="Original fit",
            revision=1,
            publication_hash="publication-hash",
            outputs=[
                AnalysisFactRecordOutput(
                    kind="fact",
                    id="comparison-request",
                    title="Request",
                    content=AnalysisFact(
                        schema_id=COMPARISON_REQUEST_SCHEMA.id,
                        schema_codec=COMPARISON_REQUEST_SCHEMA.schema_codec,
                        schema_hash=COMPARISON_REQUEST_SCHEMA.schema_hash,
                        codec="json",
                        value=COMPARISON_REQUEST_SCHEMA.encode(original),
                    ),
                )
            ],
        ),
    )
    edited = ComparisonRequest(
        action="candidate",
        primary_run="left",
        analysis_id="fit",
        analysis_hash="publication-hash",
        model_id="different",
        model_version="2",
        parameters={"offset": 99},
        secondary_run="different-run",
        actor="reviewer",
    )
    reopened = reopen_comparison(edited, source)
    assert reopened.code_revision == original.code_revision
    assert reopened.model_id == original.model_id
    assert reopened.model_version == original.model_version
    assert reopened.parameters == original.parameters
    assert reopened.primary == original.primary
    assert reopened.secondary == original.secondary
    assert reopened.secondary_run == original.secondary_run
    assert reopened.actor == "reviewer" and reopened.action == "candidate"
    for field, value in (
        ("primary_run", "other"),
        ("analysis_id", "other"),
        ("analysis_hash", "other"),
    ):
        with pytest.raises(ValueError, match="exact run/analysis/hash"):
            reopen_comparison(edited.model_copy(update={field: value}), source)
