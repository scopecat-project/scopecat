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
        records=raw.records,
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
    assert selected[0].acquisition_evidence.for_variable("signal").values[1] is None
    table = (
        dataset.project({"signal": "signal"}, diagnostics="full")
        .select_entities("entity", entities)
        .to_arrow()
    )
    expected_table = expected.project(
        {"signal": "signal"}, diagnostics="full"
    ).to_arrow()
    assert table.equals(expected_table, check_metadata=True)
