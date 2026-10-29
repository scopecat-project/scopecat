from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from scopecat.kernel.content_identity import model_wire_content_hash
from scopecat.kernel.entity import EntityRef
from scopecat.measurements.recording_arrow import (
    decode_measurement_append,
    encode_measurement_append,
    measurement_dataset_schema_hash,
)
from scopecat.records.measurement import (
    MeasurementDatasetSchema,
    MeasurementEntityIndex,
    MeasurementRecord,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
)


def _header(size: int = 128) -> MeasurementDatasetHeader:
    def scalar(value: int) -> dict[str, object]:
        return {"kind": "scalar", "dtype": "float64", "value": value}

    schema = MeasurementDatasetSchema.model_validate(
        {
            "dataset_id": "raw-measurements",
            "point_domain": {
                "kind": "product_grid",
                "axes": [
                    {
                        "id": "bias",
                        "size": size,
                        "source": {
                            "kind": "values",
                            "values": [scalar(i) for i in range(size)],
                        },
                    }
                ],
            },
            "dimensions": [{"id": "point", "kind": "point", "size": size}],
            "variables": [
                {
                    "id": "bias",
                    "role": "coordinate",
                    "dtype": "float64",
                    "dims": ["point"],
                },
                {
                    "id": "response",
                    "role": "observable",
                    "dtype": "float64",
                    "dims": ["point"],
                },
            ],
            "metadata": {"nested": {"values": [1, 2]}},
        }
    )
    return MeasurementDatasetHeader(
        run_id="identity-run",
        recording_contract_fingerprint="contract",
        dataset_schema=schema,
        expected_record_count=size,
        record_count_limit=size,
    )


def test_hashes_are_computed_once_per_instance_without_changing_wire_or_equality() -> (
    None
):
    header = _header()
    schema = header.dataset_schema
    wire = header.model_dump_json()
    reloaded = MeasurementDatasetHeader.model_validate_json(wire)
    expected_header = model_wire_content_hash(header)
    expected_schema = model_wire_content_hash(schema)
    with (
        patch.object(
            MeasurementDatasetSchema,
            "model_dump",
            autospec=True,
            side_effect=MeasurementDatasetSchema.model_dump,
        ) as schema_hash,
        patch(
            "scopecat.records.measurement_recording.model_wire_content_hash",
            wraps=model_wire_content_hash,
        ) as header_hash,
    ):
        for _ in range(3):
            assert header.content_hash == expected_header
            assert measurement_dataset_schema_hash(schema) == expected_schema
        assert schema_hash.call_count == 1
        assert header_hash.call_count == 1
        assert reloaded == header  # Cached state must not affect model equality.
        assert header.model_dump_json() == wire
        assert reloaded.content_hash == header.content_hash
        assert header_hash.call_count == 2
        changed = header.model_copy(update={"run_id": "other-run"})
        assert changed.content_hash != header.content_hash
        assert header_hash.call_count == 3
        changed_schema = schema.model_copy(update={"dataset_id": "other-dataset"})
        assert measurement_dataset_schema_hash(changed_schema) != expected_schema
        assert schema_hash.call_count == 2
        changed_header = header.model_copy(update={"dataset_schema": changed_schema})
        assert changed_header.content_hash != header.content_hash
        assert header_hash.call_count == 4
    with pytest.raises(ValidationError):
        header.model_copy(update={"expected_record_count": 1})
    with pytest.raises((AttributeError, ValidationError)):
        # Exercise runtime rejection without a static read-only assignment.
        setattr(header, "content_hash", "forged")  # noqa: B010
    with pytest.raises(TypeError):
        cast("dict[str, object]", schema.metadata)["nested"] = {}


def test_entity_metadata_cannot_mutate_a_cached_schema_through_unvalidated_copy() -> (
    None
):
    metadata = {"nested": {"values": [1, 2]}}
    entity = EntityRef(id="q").model_copy(update={"metadata": metadata})
    index = MeasurementEntityIndex(values=[entity])
    schema = _header().dataset_schema.model_copy(
        update={
            "dimensions": [
                {"id": "point", "kind": "point", "size": 128},
                {"id": "entity", "kind": "entity", "size": 1, "index": index},
            ]
        }
    )
    digest = measurement_dataset_schema_hash(schema)
    metadata["nested"]["values"].append(3)
    assert (
        measurement_dataset_schema_hash(schema)
        == digest
        == model_wire_content_hash(schema)
    )
    frozen = cast("Mapping[str, object]", index.values[0].metadata["nested"])
    assert frozen["values"] == (1, 2)
    with pytest.raises(TypeError):
        cast("dict[str, object]", frozen)["values"] = [9]
    with pytest.raises((ValueError, TypeError)):
        MeasurementEntityIndex(
            values=[
                EntityRef(id="q").model_copy(update={"metadata": {"bad": object()}})
            ]
        )


def test_reused_schema_identity_preserves_arrow_bytes_and_rejects_other_schemas() -> (
    None
):
    header = _header()
    schema = header.dataset_schema
    record = MeasurementRecord.model_validate(
        {
            "run_id": header.run_id,
            "point_index": 0,
            "coordinates": {"bias": {"kind": "scalar", "value": 0}},
            "observables": {"response": {"kind": "scalar", "value": 2}},
        }
    )
    append = MeasurementDatasetAppend(
        run_id=header.run_id,
        header_content_hash=header.content_hash,
        acquisition_start=0,
        records=(record,),
    )
    uncached_hash = model_wire_content_hash(schema)
    original = encode_measurement_append(
        append, schema, dataset_schema_hash=uncached_hash
    )
    assert encode_measurement_append(append, schema) == original
    assert (
        decode_measurement_append(original, schema).content_hash == append.content_hash
    )
    reloaded = MeasurementDatasetSchema.model_validate_json(schema.model_dump_json())
    assert (
        decode_measurement_append(original, reloaded).content_hash
        == append.content_hash
    )
    changed = schema.model_copy(update={"dataset_id": "other"})
    with pytest.raises(ValueError, match="schema"):
        decode_measurement_append(original, changed)
    with pytest.raises(ValueError, match="schema"):
        decode_measurement_append(original, schema, dataset_schema_hash="wrong")
    forged = encode_measurement_append(append, schema, dataset_schema_hash="wrong")
    with pytest.raises(ValueError, match="schema"):
        decode_measurement_append(forged, schema)
