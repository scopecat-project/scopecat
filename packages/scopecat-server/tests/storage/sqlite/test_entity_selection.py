"""Native selected decoding agrees with the existing materialized entity semantics."""

import pytest
from scopecat.kernel.entity import EntityRef
from scopecat.measurements.dataset import Dataset
from scopecat.measurements.entity_selection import (
    MeasurementEntitySelection,
    bind_entity_selection,
)
from scopecat.measurements.recording_arrow import (
    decode_measurement_record_indices,
    encode_measurement_append,
)
from scopecat.records.content import ContentEntry
from scopecat.records.measurement import EntityAcquisitionEvidence
from scopecat.records.measurement_recording import MeasurementDatasetAppend
from scopecat_testkit.entity_reads import wide_entity_measurements


@pytest.mark.parametrize("complex_values", [False, True])
def test_native_selected_entities_preserve_order_missingness_and_evidence(
    complex_values: bool,
) -> None:
    raw = wide_entity_measurements(complex_values=complex_values)
    entities = (
        EntityRef(kind="qubit", id="q7"),
        EntityRef(kind="qubit", id="absent"),
        EntityRef(kind="qubit", id="q0"),
    )
    selection = MeasurementEntitySelection(dimension_id="entity", entities=entities)
    append = MeasurementDatasetAppend(
        run_id="run-wide",
        header_content_hash="header",
        acquisition_start=0,
        records=tuple(raw.records),
    )
    encoded = encode_measurement_append(append, raw.dataset_schema)
    selected = decode_measurement_record_indices(
        encoded, raw.dataset_schema, (1, 0), entity_selection=selection
    )
    entry = ContentEntry(
        role="dataset",
        id="raw-measurements",
        kind="measurement_dataset",
        schema=raw.dataset_schema.model_dump(mode="json"),
        content_hash="source",
    )
    full = decode_measurement_record_indices(encoded, raw.dataset_schema, (0, 1))
    dataset = Dataset(raw.model_copy(update={"records": full}), entry)
    expected = dataset.reindex_entities("entity", entities)
    assert [record.model_dump(mode="json") for record in selected] == [
        record.model_dump(mode="json") for record in reversed(expected.records)
    ]
    bound = bind_entity_selection(raw.dataset_schema, selection)
    assert bound.schema == expected.schema
    assert bound.selection.entities[0].metadata["label"] == "run-wide:Q7"
    evidence = selected[0].acquisition_evidence.for_variable("signal")
    assert isinstance(evidence, EntityAcquisitionEvidence)
    assert evidence.values[1] is None
    table = (
        dataset.project({"signal": "signal"}, diagnostics="full")
        .select_entities("entity", entities)
        .to_arrow()
    )
    expected_table = expected.project(
        {"signal": "signal"}, diagnostics="full"
    ).to_arrow()
    assert table.equals(expected_table, check_metadata=True)


@pytest.mark.parametrize("layout", ["trailing_entity", "partitioned", "segmented"])
def test_native_selection_matches_materialized_alignment_for_array_layouts(
    layout: str,
) -> None:
    from scopecat.records.measurement import (
        MeasurementArray,
        MeasurementPartitionedArray,
        MeasurementSegmentedArray,
    )

    raw = wide_entity_measurements(point_count=1)
    record = raw.records[0]
    signal = record.observables["signal"]
    assert isinstance(signal, MeasurementArray)
    # Layout qualification uses valid values; failure remapping is covered above.
    values = signal.values
    schema = raw.dataset_schema
    if layout == "trailing_entity":
        value = MeasurementArray.create(values=values.T, unit="V")
        schema = schema.model_copy(
            update={
                "variables": tuple(
                    variable.model_copy(update={"dims": ("point", "sample", "entity")})
                    if variable.id == "signal"
                    else variable
                    for variable in schema.variables
                )
            }
        )
    elif layout == "partitioned":
        value = MeasurementPartitionedArray.create(
            axis=0,
            unit="V",
            partitions=(
                MeasurementArray.create(values=values[:4], unit="V"),
                MeasurementArray.create(values=values[4:], unit="V"),
            ),
        )
    else:
        value = MeasurementSegmentedArray.create(
            unit="V",
            segments=tuple(
                MeasurementArray.create(values=row[: index + 1], unit="V")
                for index, row in enumerate(values)
            ),
        )
        schema = schema.model_copy(
            update={
                "dimensions": tuple(
                    dimension.model_copy(update={"kind": "record_axis", "size": None})
                    if dimension.id == "sample"
                    else dimension
                    for dimension in schema.dimensions
                )
            }
        )
    records = (record.model_copy(update={"observables": {"signal": value}}),)
    encoded = encode_measurement_append(
        MeasurementDatasetAppend(
            run_id="run-wide",
            header_content_hash="header",
            acquisition_start=0,
            records=records,
        ),
        schema,
    )
    full = decode_measurement_record_indices(encoded, schema, (0,))
    entry = ContentEntry(
        role="dataset",
        id="raw-measurements",
        kind="measurement_dataset",
        schema=schema.model_dump(mode="json"),
        content_hash="source",
    )
    dataset = Dataset(
        raw.model_copy(update={"dataset_schema": schema, "records": full}), entry
    )
    selection = MeasurementEntitySelection(
        dimension_id="entity",
        entities=(
            EntityRef(id="q7", kind="qubit"),
            EntityRef(id="absent", kind="qubit"),
            EntityRef(id="q0", kind="qubit"),
        ),
    )
    expected = dataset.reindex_entities("entity", selection.entities)
    selected = decode_measurement_record_indices(
        encoded, schema, (0,), entity_selection=selection
    )
    assert selected[0].model_dump(mode="json") == expected.records[0].model_dump(
        mode="json"
    )


def test_http_entity_selection_budget_counts_missing_identities() -> None:
    from pydantic import ValidationError
    from scopecat.daemon.views import (
        MeasurementArrowQuery,
        MeasurementTracePreviewQuery,
    )

    entities = [{"kind": "qubit", "id": f"missing-{index}"} for index in range(33)]
    with pytest.raises(ValidationError, match="at most 32"):
        MeasurementArrowQuery.model_validate(
            {
                "columns": [{"name": "signal", "variable_id": "signal"}],
                "entity_selection": {"dimension_id": "entity", "entities": entities},
            }
        )
    with pytest.raises(ValidationError, match="at most 32"):
        MeasurementTracePreviewQuery.model_validate(
            {"observable_id": "signal", "entities": entities}
        )
