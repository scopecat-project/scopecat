# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import perf_counter

import numpy as np
import pyarrow as pa
import pytest
from scopecat.measurements.recording_arrow import (
    MEASUREMENT_APPEND_ARROW_FORMAT,
    MeasurementArrowCodecError,
    decode_measurement_append,
    decode_measurement_record_indices,
    decode_measurement_record_slice,
    encode_measurement_append,
)
from scopecat.program.measurement_types import MeasurementDType
from scopecat.records.measurement import (
    InstrumentAcquisitionEvidence,
    MeasurementAcquisitionEvidenceCatalog,
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementPartitionedArray,
    MeasurementPointCloudPointDomain,
    MeasurementPointDomainColumn,
    MeasurementProductGridPointDomain,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementUnavailable,
    MeasurementValue,
    MeasurementVariable,
)
from scopecat.records.measurement_recording import MeasurementDatasetAppend


def _append() -> MeasurementDatasetAppend:
    run_id = "run-arrow"
    evidence = InstrumentAcquisitionEvidence(
        command_id="collect-signal",
        instrument_id="scope",
        interface_id="test.waveform/v1",
        component_path=("channel", "1"),
        acquisition_id="acquisition-0",
        result_id="signal",
        started_at=datetime(
            2026,
            8,
            4,
            9,
            tzinfo=timezone(timedelta(hours=8)),
        ),
        completed_at=datetime(
            2026,
            8,
            4,
            9,
            0,
            1,
            tzinfo=timezone(timedelta(hours=8)),
        ),
    )
    record = MeasurementRecord(
        run_id=run_id,
        logical_point_id="point-0",
        point_index=0,
        coordinates={
            "shot": MeasurementScalar.create(dtype="int64", value=2),
        },
        observables={
            "estimate": MeasurementScalar.create(dtype="float64", value=1),
            "iq": MeasurementScalar.create(
                dtype="complex128",
                value=complex(0.25, -0.5),
            ),
            "trace": MeasurementArray.create(
                dtype="float64",
                unit="V",
                values=np.array([[1.0, 2.0], [3.0, 4.0]]),
                metadata={"calibrated": True},
            ),
            "counts": MeasurementArray.create(
                dtype="int64",
                values=[3, 5],
            ),
            "mask": MeasurementArray.create(
                dtype="bool",
                values=[True, False],
            ),
            "labels": MeasurementArray.create(
                dtype="string",
                values=["准备", "done"],
            ),
            "complex_trace": MeasurementArray.create(
                dtype="complex128",
                unit="ratio",
                values=np.array([1 + 2j, 3 - 4j]),
            ),
            "ragged_trace": MeasurementArray.create(
                dtype="float64",
                unit="V",
                values=np.array([0.5, 1.5, 2.5]),
            ),
            "missing": MeasurementUnavailable.create(
                reason="overload",
                dtype="float64",
                unit="V",
                shape=(None,),
                metadata={"status_register": 4},
            ),
        },
        acquisition_evidence=MeasurementAcquisitionEvidenceCatalog.create(
            {"trace": evidence}
        ),
        metadata={"note": "Arrow IPC", "nested": {"revision": 1}},
    )
    return MeasurementDatasetAppend(
        run_id=run_id,
        header_content_hash="header-hash",
        acquisition_start=0,
        records=(record,),
    )


def _schema(*, point_count: int = 1) -> MeasurementDatasetSchema:
    return MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementPointCloudPointDomain(
            columns=(MeasurementPointDomainColumn(id="shot"),)
        ),
        dimensions=(
            MeasurementDimension(id="point", kind="point", size=point_count),
            MeasurementDimension(id="row", kind="record_axis", size=2),
            MeasurementDimension(id="column", kind="record_axis", size=2),
            MeasurementDimension(id="sample", kind="record_axis", size=2),
            MeasurementDimension(id="ragged", kind="record_axis", size=None),
        ),
        variables=(
            MeasurementVariable(
                id="shot", role="coordinate", dtype="int64", dims=("point",)
            ),
            MeasurementVariable(
                id="estimate", role="observable", dtype="float64", dims=("point",)
            ),
            MeasurementVariable(
                id="iq", role="observable", dtype="complex128", dims=("point",)
            ),
            MeasurementVariable(
                id="trace",
                role="observable",
                dtype="float64",
                unit="V",
                dims=("point", "row", "column"),
            ),
            MeasurementVariable(
                id="counts",
                role="observable",
                dtype="int64",
                dims=("point", "sample"),
            ),
            MeasurementVariable(
                id="mask",
                role="observable",
                dtype="bool",
                dims=("point", "sample"),
            ),
            MeasurementVariable(
                id="labels",
                role="observable",
                dtype="string",
                dims=("point", "sample"),
            ),
            MeasurementVariable(
                id="complex_trace",
                role="observable",
                dtype="complex128",
                unit="ratio",
                dims=("point", "sample"),
            ),
            MeasurementVariable(
                id="ragged_trace",
                role="observable",
                dtype="float64",
                unit="V",
                dims=("point", "ragged"),
            ),
            MeasurementVariable(
                id="missing",
                role="observable",
                dtype="float64",
                unit="V",
                dims=("point", "ragged"),
            ),
        ),
    )


def test_measurement_append_round_trips_as_typed_arrow_ipc() -> None:
    append = _append()
    schema = _schema()

    content = encode_measurement_append(append, schema)
    restored = decode_measurement_append(content, schema)

    assert content.startswith(b"ARROW1")
    assert encode_measurement_append(append, schema) == content
    assert restored == append
    assert restored.operation_id == append.operation_id
    assert restored.content_hash == append.content_hash
    trace = restored.records[0].observables["trace"]
    assert isinstance(trace, MeasurementArray)
    assert trace.values.dtype == np.dtype(np.float64)
    assert trace.values.flags.c_contiguous
    assert not trace.values.flags.writeable
    complex_trace = restored.records[0].observables["complex_trace"]
    assert isinstance(complex_trace, MeasurementArray)
    np.testing.assert_array_equal(
        complex_trace.values,
        np.array([1 + 2j, 3 - 4j], dtype=np.complex128),
    )

    reader = pa.ipc.open_file(pa.BufferReader(content))
    assert reader.num_record_batches == 1
    assert reader.schema.metadata[b"scopecat.format"] == (
        MEASUREMENT_APPEND_ARROW_FORMAT.encode()
    )
    assert reader.schema.metadata[b"scopecat.content_hash"] == (
        append.content_hash.encode()
    )
    assert "coordinates" not in reader.schema.names
    assert "observables" not in reader.schema.names
    assert reader.schema.field("value:trace").type == pa.list_(
        pa.field(
            "item",
            pa.list_(
                pa.field("item", pa.float64()),
                2,
            ),
        ),
        2,
    )
    assert reader.schema.field("value:missing").type == pa.large_list(
        pa.field("item", pa.float64())
    )
    assert reader.schema.field("value:complex_trace").type.value_type == pa.struct(
        [
            pa.field("real", pa.float64(), nullable=False),
            pa.field("imag", pa.float64(), nullable=False),
        ]
    )
    ragged_trace = restored.records[0].observables["ragged_trace"]
    assert isinstance(ragged_trace, MeasurementArray)
    assert ragged_trace.shape == (3,)


def test_megawaveform_binary_round_trip_meets_live_transport_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_count = 1 << 20
    values = np.linspace(-1.0, 1.0, sample_count, dtype=np.float64)
    schema = MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementProductGridPointDomain(axes=()),
        dimensions=(
            MeasurementDimension(id="point", kind="point", size=1),
            MeasurementDimension(id="sample", kind="record_axis", size=sample_count),
        ),
        variables=(
            MeasurementVariable(
                id="trace",
                role="observable",
                dtype="float64",
                unit="V",
                dims=("point", "sample"),
            ),
        ),
    )
    append = MeasurementDatasetAppend(
        run_id="run-live-waveform",
        header_content_hash="header-hash",
        acquisition_start=0,
        records=(
            MeasurementRecord(
                run_id="run-live-waveform",
                logical_point_id="point-0",
                point_index=0,
                coordinates={},
                observables={
                    "trace": MeasurementArray.create(
                        dtype="float64",
                        unit="V",
                        values=values,
                    )
                },
            ),
        ),
    )

    def reject_json_dump(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("measurement transport must not serialize arrays to JSON")

    monkeypatch.setattr(MeasurementDatasetAppend, "model_dump", reject_json_dump)
    started = perf_counter()
    content = encode_measurement_append(append, schema)
    restored = decode_measurement_append(content, schema)
    elapsed = perf_counter() - started
    restored_trace = restored.records[0].observables["trace"]

    assert len(content) < values.nbytes + 64 * 1024
    assert isinstance(restored_trace, MeasurementArray)
    assert np.array_equal(restored_trace.values, values)
    assert elapsed < 2.0


def test_measurement_arrow_selection_decodes_only_requested_rows() -> None:
    first = _append()
    template = first.records[0]
    append = first.model_copy(
        update={
            "records": tuple(
                template.model_copy(
                    update={
                        "logical_point_id": f"point-{index}",
                        "point_index": index,
                    }
                )
                for index in range(3)
            )
        }
    )
    schema = _schema(point_count=3)
    content = encode_measurement_append(append, schema)

    page = decode_measurement_record_slice(
        content,
        schema,
        offset=1,
        length=2,
        variable_ids=("trace",),
    )
    selected = decode_measurement_record_indices(content, schema, (2, 0))

    assert [record.point_index for record in page] == [1, 2]
    assert page[0].coordinates == {}
    assert set(page[0].observables) == {"trace"}
    assert set(page[0].acquisition_evidence.variable_refs) == {"trace"}
    assert [record.point_index for record in selected] == [2, 0]


def test_measurement_arrow_encoding_is_independent_of_mapping_insertion_order() -> None:
    append = _append()
    schema = _schema()
    record = append.records[0]
    reordered = record.model_copy(
        update={
            "observables": dict(reversed(tuple(record.observables.items()))),
        }
    )
    reordered_append = append.model_copy(update={"records": (reordered,)})

    assert reordered_append.content_hash == append.content_hash
    assert encode_measurement_append(
        reordered_append,
        schema,
    ) == encode_measurement_append(
        append,
        schema,
    )


def test_measurement_arrow_rejects_non_ipc_payloads() -> None:
    with pytest.raises(MeasurementArrowCodecError, match="invalid measurement Arrow"):
        decode_measurement_append(b'{"records": []}', _schema())


@pytest.mark.parametrize("column", ["__scopecat.record_metadata", "metadata:estimate"])
@pytest.mark.parametrize(
    "payload,valid",
    [
        (b"{}", True),
        (b" { } ", True),
        (b'{"nested":{"value":1}}', True),
        (b"{}garbage", False),
        (b"[]", False),
        (b'{"value":NaN}', False),
        (b"", False),
        (b"\xff", False),
    ],
)
def test_selected_arrow_reads_validate_empty_and_nonempty_metadata(
    column: str, payload: bytes, *, valid: bool
) -> None:
    import json

    schema = _schema()
    content = encode_measurement_append(_append(), schema)
    batch = pa.ipc.open_file(pa.BufferReader(content)).get_batch(0)
    index = batch.schema.get_field_index(column)
    batch = batch.set_column(
        index, batch.schema.field(index), pa.array([payload], type=pa.large_binary())
    )
    sink = pa.BufferOutputStream()
    with pa.ipc.new_file(sink, batch.schema) as writer:
        writer.write_batch(batch)
    changed = sink.getvalue().to_pybytes()

    if not valid:
        with pytest.raises(MeasurementArrowCodecError, match="metadata"):
            decode_measurement_record_indices(
                changed, schema, (0,), variable_ids=("estimate",)
            )
        return

    [record] = decode_measurement_record_indices(
        changed, schema, (0,), variable_ids=("estimate",)
    )
    metadata = (
        record.metadata
        if column == "__scopecat.record_metadata"
        else record.observables["estimate"].metadata
    )
    assert metadata == json.loads(payload)
    assert record.observables["estimate"] == _append().records[0].observables[
        "estimate"
    ].model_copy(update={"metadata": metadata if column == "metadata:estimate" else {}})


@pytest.mark.parametrize(
    "dtype,values",
    [
        ("float64", [1.5, 999.0]),
        ("int64", [2, 999]),
        ("complex128", [1 + 2j, 9 + 8j]),
        ("bool", [False, True]),
        ("string", ["ok", "long invalid filler"]),
    ],
)
@pytest.mark.parametrize("partitioned", [False, True])
def test_masked_fill_does_not_change_arrow_record_identity(
    dtype: MeasurementDType, values: list[object], partitioned: bool
) -> None:
    schema = MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementProductGridPointDomain(axes=()),
        dimensions=(
            MeasurementDimension(id="point", kind="point", size=1),
            MeasurementDimension(id="sample", kind="sample", size=2),
        ),
        variables=(
            MeasurementVariable(
                id="signal", role="observable", dtype=dtype, dims=("point", "sample")
            ),
        ),
    )
    array = MeasurementArray.create(
        dtype=dtype,
        values=values,
        availability=MeasurementArrayAvailability.create(
            valid=[True, False], reason="missing"
        ),
    )

    def append_for(value: MeasurementValue) -> MeasurementDatasetAppend:
        return MeasurementDatasetAppend(
            run_id="fill",
            header_content_hash="sha256:" + "0" * 64,
            acquisition_start=0,
            records=(
                MeasurementRecord(
                    run_id="fill",
                    point_index=0,
                    coordinates={},
                    observables={"signal": value},
                ),
            ),
        )

    wire_value: MeasurementValue = array
    if partitioned:
        wire_value = MeasurementPartitionedArray.create(
            axis=0,
            dtype=dtype,
            partitions=(
                MeasurementArray.create(dtype=dtype, values=values[:1]),
                MeasurementArray.create(
                    dtype=dtype,
                    values=values[1:],
                    availability=MeasurementArrayAvailability.create(
                        valid=[False], reason="missing"
                    ),
                ),
            ),
        )
    original = append_for(wire_value)
    assert original.content_hash == append_for(array).content_hash
    restored = decode_measurement_append(
        encode_measurement_append(original, schema), schema
    )
    assert original.content_hash == restored.content_hash
    assert original.record_content_hashes == restored.record_content_hashes
    decoded = restored.records[0].observables["signal"]
    if isinstance(decoded, MeasurementPartitionedArray):
        decoded = decoded.materialize()
    assert isinstance(decoded, MeasurementArray)
    assert decoded.values[1] == ("" if dtype == "string" else 0)
    assert array.values[1] == values[1]
    assert decoded.availability == array.availability
    changed = MeasurementArray.create(
        dtype=dtype, values=[values[1], values[1]], availability=array.availability
    )
    assert append_for(changed).content_hash != original.content_hash

    changed_reason = MeasurementArray.create(
        dtype=dtype,
        values=values,
        availability=MeasurementArrayAvailability.create(
            valid=[True, False], reason="overload"
        ),
    )
    changed_mask = MeasurementArray.create(
        dtype=dtype,
        values=values,
        availability=MeasurementArrayAvailability.create(
            valid=[False, True], reason="missing"
        ),
    )
    assert append_for(changed_reason).content_hash != original.content_hash
    assert append_for(changed_mask).content_hash != original.content_hash


def test_nonleading_partitions_hash_masked_values_in_logical_order() -> None:
    values = np.asarray([[1.0, 999.0], [888.0, 4.0]])
    valid = np.asarray([[True, False], [False, True]])
    partitioned = MeasurementPartitionedArray.create(
        axis=1,
        partitions=tuple(
            MeasurementArray.create(
                values=values[:, i : i + 1],
                availability=MeasurementArrayAvailability.create(
                    valid=valid[:, i : i + 1], reason="missing"
                ),
            )
            for i in range(2)
        ),
    )

    def identity(value: MeasurementValue) -> str:
        return MeasurementDatasetAppend(
            run_id="partitioned",
            header_content_hash="sha256:" + "0" * 64,
            acquisition_start=0,
            records=(
                MeasurementRecord(
                    run_id="partitioned",
                    point_index=0,
                    coordinates={},
                    observables={"signal": value},
                ),
            ),
        ).content_hash

    dense = MeasurementArray.create(
        values=values, availability=partitioned.availability
    )
    assert identity(dense) == identity(partitioned)
    np.testing.assert_array_equal(values, [[1.0, 999.0], [888.0, 4.0]])
