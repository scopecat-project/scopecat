from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast, override

import numpy as np
import pytest
from scopecat_testkit.execution_fakes import FakeMeasurementDatasetRepository
from scopecat_testkit.measurement_arrow_fixture import (
    ui_measurement_arrow_fixture,
    ui_measurement_arrow_fixture_schema,
)
from scopecat_testkit.measurement_assembly import (
    assembled_measurement_values_for_all_uses,
)

from scopecat.execution.measurement_recording import (
    initialize_measurement_dataset,
    seal_measurement_dataset,
)
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.errors import MeasurementRecordingError
from scopecat.measurements.projection import (
    ProjectedMeasurementDataset,
    project_measurement_records,
    select_measurement_projection,
)
from scopecat.measurements.recording_arrow import (
    decode_measurement_append,
    encode_measurement_append,
)
from scopecat.records.measurement import (
    EntityAcquisitionEvidence,
    InstrumentAcquisitionEvidence,
    MeasurementAcquisitionEvidenceCatalog,
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementEntityIndex,
    MeasurementPartitionedArray,
    MeasurementPointCloudPointDomain,
    MeasurementRecord,
    MeasurementVariable,
)
from scopecat.records.measurement_recording import (
    CANONICAL_MEASUREMENT_DATASET_REF,
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
    MeasurementDatasetReceipt,
    MeasurementDatasetSeal,
    measurement_dataset_content_hash,
    measurement_fragment_content_hash,
    measurement_record_content_hash,
)


def _projected(*, run_id: str = "recording-run") -> ProjectedMeasurementDataset:
    scenario, assembled = assembled_measurement_values_for_all_uses()
    projection = select_measurement_projection(scenario.catalog, scenario.records)
    return project_measurement_records(
        projection, assembled, run_id=run_id, points=scenario.points
    )


def test_complex_arrow_roundtrip_preserves_signed_zero_and_content_identity() -> None:
    values = np.asarray(
        [
            complex(real, imag)
            for real, imag in ((0.0, -0.0), (-0.0, 0.0), (-0.0, -0.0), (1.0, -0.0))
        ]
    )
    schema = MeasurementDatasetSchema(
        dataset_id="raw",
        point_domain=MeasurementPointCloudPointDomain(columns=()),
        dimensions=(
            MeasurementDimension(id="point", kind="point", size=1),
            MeasurementDimension(id="shot", kind="shot", size=4),
        ),
        variables=(
            MeasurementVariable(
                id="iq",
                role="observable",
                dtype="complex128",
                unit="ratio",
                dims=("point", "shot"),
            ),
        ),
    )
    append = MeasurementDatasetAppend(
        run_id="signed-zero",
        header_content_hash="sha256:header",
        acquisition_start=0,
        records=(
            MeasurementRecord(
                run_id="signed-zero",
                point_index=0,
                coordinates={},
                observables={
                    "iq": MeasurementArray.create(
                        dtype="complex128", unit="ratio", values=values
                    )
                },
            ),
        ),
    )
    restored = decode_measurement_append(
        encode_measurement_append(append, schema), schema
    )
    assert restored.content_hash == append.content_hash
    iq = restored.records[0].observables["iq"]
    assert isinstance(iq, MeasurementArray)
    assert iq.values.tobytes() == values.tobytes()


def _seal(
    projected: ProjectedMeasurementDataset,
    writer: FakeMeasurementDatasetRepository,
    header: MeasurementDatasetHeader,
) -> MeasurementDatasetReceipt:
    return seal_measurement_dataset(
        run_id=projected.run_id,
        header=header,
        record_count=len(projected.records),
        record_content_hashes=tuple(
            measurement_record_content_hash(record) for record in projected.records
        ),
        writer=writer,
    )


def _header(projected: ProjectedMeasurementDataset) -> MeasurementDatasetHeader:
    schema = projected.projection.schema
    assert schema is not None
    return MeasurementDatasetHeader(
        run_id=projected.run_id,
        recording_contract_fingerprint=projected.recording_contract_fingerprint,
        dataset_schema=schema,
        expected_record_count=projected.projection.catalog.point_contract.point_count,
        record_count_limit=projected.projection.catalog.point_contract.point_limit,
    )


def test_recording_initializes_and_seals_one_canonical_dataset() -> None:
    projected = _projected()
    writer = FakeMeasurementDatasetRepository()
    header = _header(projected)

    header_receipt = initialize_measurement_dataset(header, writer)
    append_receipt = writer.append(
        MeasurementDatasetAppend(
            run_id=projected.run_id,
            header_content_hash=header.content_hash,
            acquisition_start=0,
            records=projected.records,
        )
    )
    seal_receipt = _seal(projected, writer, header)

    [append] = writer.appends
    assert append.records == projected.records
    assert header_receipt.dataset_ref == CANONICAL_MEASUREMENT_DATASET_REF
    assert append_receipt.dataset_ref == CANONICAL_MEASUREMENT_DATASET_REF
    assert seal_receipt.dataset_ref == CANONICAL_MEASUREMENT_DATASET_REF


def test_arrow_recording_round_trips_entity_arrays_with_partial_availability() -> None:
    entities = (
        EntityRef(id="q0", kind="logical_qubit"),
        EntityRef(id="q1", kind="logical_qubit"),
    )
    schema = MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementPointCloudPointDomain(columns=()),
        dimensions=(
            MeasurementDimension(id="point", kind="point", size=1),
            MeasurementDimension(
                id="qubit",
                kind="entity",
                size=2,
                index=MeasurementEntityIndex(
                    values=entities,
                ),
            ),
            MeasurementDimension(id="shot", kind="shot", size=2),
        ),
        variables=(
            MeasurementVariable(
                id="iq",
                role="observable",
                dtype="complex128",
                unit="ratio",
                dims=("point", "qubit", "shot"),
            ),
        ),
        primary_observables=("iq",),
    )
    availability = MeasurementArrayAvailability.create(
        valid=[[True, True], [False, True]],
        reason="missing",
        metadata={"entity": "q1"},
    )
    evidence = InstrumentAcquisitionEvidence(
        command_id="readout",
        instrument_id="digitizer",
        interface_id="test.readout/v1",
        acquisition_id="iq",
        result_id="iq",
        started_at=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
        completed_at=datetime(2026, 8, 15, 10, 0, 1, tzinfo=UTC),
    )
    record = MeasurementRecord(
        run_id="entity-run",
        point_index=0,
        coordinates={},
        observables={
            "iq": MeasurementArray.create(
                dtype="complex128",
                unit="ratio",
                values=[[1 + 2j, 3 + 4j], [0j, 5 + 6j]],
                availability=availability,
            )
        },
        acquisition_evidence=MeasurementAcquisitionEvidenceCatalog.create(
            {
                "iq": EntityAcquisitionEvidence(
                    dimension_id="qubit",
                    values=(evidence, None),
                )
            }
        ),
    )
    append = MeasurementDatasetAppend(
        run_id="entity-run",
        header_content_hash="sha256:header",
        acquisition_start=0,
        records=(record,),
    )

    restored = decode_measurement_append(
        encode_measurement_append(append, schema),
        schema,
    )

    assert restored == append
    iq = restored.records[0].observables["iq"]
    assert isinstance(iq, MeasurementArray)
    assert iq.availability is not None
    assert iq.availability.valid.tolist() == [[True, True], [False, True]]


@pytest.mark.parametrize("shot_size", [5, None])
def test_arrow_recording_preserves_shot_partitions(shot_size: int | None) -> None:
    schema = MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementPointCloudPointDomain(columns=()),
        dimensions=(
            MeasurementDimension(id="point", kind="point", size=1),
            MeasurementDimension(id="channel", kind="channel", size=2),
            MeasurementDimension(id="shot", kind="shot", size=shot_size),
        ),
        variables=(
            MeasurementVariable(
                id="iq",
                role="observable",
                dtype="complex128",
                unit="ratio",
                dims=("point", "channel", "shot"),
            ),
        ),
        primary_observables=("iq",),
    )
    chunks = (
        MeasurementArray.create(
            values=[[1 + 1j, 2 + 2j], [6 + 6j, 7 + 7j]],
            dtype="complex128",
            unit="ratio",
        ),
        MeasurementArray.create(
            values=[[3 + 3j, 0j, 5 + 5j], [8 + 8j, 9 + 9j, 10 + 10j]],
            dtype="complex128",
            unit="ratio",
            availability=MeasurementArrayAvailability.create(
                valid=[[True, False, True], [True, True, True]],
                metadata={"chunk": 1},
            ),
        ),
    )
    partitioned = MeasurementPartitionedArray.create(
        partitions=chunks,
        axis=1,
        dtype="complex128",
        unit="ratio",
    )
    record = MeasurementRecord(
        run_id="partitioned-run",
        point_index=0,
        coordinates={},
        observables={"iq": partitioned},
    )
    append = MeasurementDatasetAppend(
        run_id="partitioned-run",
        header_content_hash="sha256:header",
        acquisition_start=0,
        records=(record,),
    )

    restored = decode_measurement_append(
        encode_measurement_append(append, schema),
        schema,
    )

    assert restored == append
    iq = restored.records[0].observables["iq"]
    assert isinstance(iq, MeasurementPartitionedArray)
    assert [partition.shape for partition in iq.partitions] == [(2, 2), (2, 3)]
    assert iq.values.tolist() == [
        [1 + 1j, 2 + 2j, 3 + 3j, 0j, 5 + 5j],
        [6 + 6j, 7 + 7j, 8 + 8j, 9 + 9j, 10 + 10j],
    ]
    assert iq.availability is not None
    assert iq.availability.valid.tolist() == [
        [True, True, True, False, True],
        [True, True, True, True, True],
    ]


def test_measurement_record_identity_is_independent_of_array_partitions() -> None:
    dense = MeasurementArray.create(
        values=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
        dtype="float64",
    )
    partitioned = MeasurementPartitionedArray.create(
        partitions=(
            MeasurementArray.create(values=[[1.0], [4.0]], dtype="float64"),
            MeasurementArray.create(values=[[2.0, 3.0], [5.0, 6.0]], dtype="float64"),
        ),
        axis=1,
        dtype="float64",
    )

    def record(
        value: MeasurementArray | MeasurementPartitionedArray,
    ) -> MeasurementRecord:
        return MeasurementRecord(
            run_id="partition-neutral",
            point_index=0,
            coordinates={},
            observables={"signal": value},
        )

    assert measurement_record_content_hash(record(dense)) == (
        measurement_record_content_hash(record(partitioned))
    )


def test_ui_arrow_fixture_is_generated_by_the_current_python_codec() -> None:
    content = ui_measurement_arrow_fixture()
    fixture = (
        Path(__file__).resolve().parents[4]
        / "apps"
        / "scopecat-ui"
        / "src"
        / "features"
        / "runs"
        / "test-fixtures"
        / "measurement-append-v11.arrow"
    )

    restored = decode_measurement_append(
        content,
        ui_measurement_arrow_fixture_schema(),
    )

    assert fixture.read_bytes() == content
    assert restored.records[0].logical_point_id == "point-7"
    assert restored.records[0].point_index == 7
    assert (
        encode_measurement_append(
            restored,
            ui_measurement_arrow_fixture_schema(),
        )
        == content
    )


def test_append_identity_is_stable_and_content_detects_conflict() -> None:
    projected = _projected()
    header = _header(projected)
    append = MeasurementDatasetAppend(
        run_id=projected.run_id,
        header_content_hash=header.content_hash,
        acquisition_start=0,
        records=projected.records,
    )
    changed = append.model_copy(
        update={
            "records": (
                *append.records[:-1],
                append.records[-1].model_copy(update={"metadata": {"changed": True}}),
            )
        }
    )
    changed_header = header.model_copy(
        update={
            "dataset_schema": header.dataset_schema.model_copy(
                update={"metadata": {"revision": 2}}
            )
        }
    )
    assert changed.operation_id == append.operation_id
    assert changed.content_hash != append.content_hash
    assert changed_header.operation_id == header.operation_id
    assert changed_header.content_hash != header.content_hash


def test_dataset_identity_is_independent_of_append_chunk_boundaries() -> None:
    projected = _projected()
    header = _header(projected)
    records = projected.records
    whole = MeasurementDatasetAppend(
        run_id=projected.run_id,
        header_content_hash=header.content_hash,
        acquisition_start=0,
        records=records,
    )
    split_at = len(records) // 2
    first = whole.model_copy(update={"records": records[:split_at]})
    second = whole.model_copy(
        update={
            "acquisition_start": split_at,
            "records": records[split_at:],
        }
    )

    whole_hash = measurement_dataset_content_hash(
        header_content_hash=header.content_hash,
        record_content_hashes=whole.record_content_hashes,
    )
    split_hash = measurement_dataset_content_hash(
        header_content_hash=header.content_hash,
        record_content_hashes=(
            *first.record_content_hashes,
            *second.record_content_hashes,
        ),
    )
    whole_fragment_hash = measurement_fragment_content_hash(
        header_content_hash=header.content_hash,
        record_content_hashes=whole.record_content_hashes,
    )
    split_fragment_hash = measurement_fragment_content_hash(
        header_content_hash=header.content_hash,
        record_content_hashes=(
            *first.record_content_hashes,
            *second.record_content_hashes,
        ),
    )

    assert whole_hash == split_hash
    assert whole_fragment_hash == split_fragment_hash


def test_header_and_append_content_hashes_cannot_change_after_construction() -> None:
    projected = _projected()
    header = _header(projected)
    append = MeasurementDatasetAppend(
        run_id=projected.run_id,
        header_content_hash=header.content_hash,
        acquisition_start=0,
        records=projected.records,
    )
    header_hash = header.content_hash
    append_hash = append.content_hash

    with pytest.raises(TypeError, match="frozen mapping is immutable"):
        cast("dict[str, object]", header.dataset_schema.metadata)["revision"] = 2
    with pytest.raises(TypeError, match="frozen mapping is immutable"):
        cast("dict[str, object]", append.records[0].metadata)["changed"] = True

    assert header.content_hash == header_hash
    assert append.content_hash == append_hash


class _InvalidReceiptWriter(FakeMeasurementDatasetRepository):
    @override
    def seal(self, seal: MeasurementDatasetSeal) -> MeasurementDatasetReceipt:
        return MeasurementDatasetReceipt(
            operation_id="wrong-seal-operation",
            dataset_content_hash="sha256:wrong",
            acquisition_record_count=0,
        )


def test_invalid_seal_receipt_terminalizes_uncertain_operation() -> None:
    projected = _projected()
    header = _header(projected)
    writer = _InvalidReceiptWriter()
    initialize_measurement_dataset(header, writer)
    with pytest.raises(MeasurementRecordingError) as caught:
        _seal(projected, writer, header)
    assert caught.value.write_may_have_completed
    assert caught.value.receipt is not None


def test_initialize_and_seal_replay_are_idempotent() -> None:
    projected = _projected()
    writer = FakeMeasurementDatasetRepository()
    header = _header(projected)
    initialized = initialize_measurement_dataset(header, writer)
    repeated = initialize_measurement_dataset(header, writer)
    writer.append(
        MeasurementDatasetAppend(
            run_id=projected.run_id,
            header_content_hash=header.content_hash,
            acquisition_start=0,
            records=projected.records,
        )
    )
    first_seal = _seal(projected, writer, header)
    repeated_seal = _seal(projected, writer, header)
    assert repeated == initialized
    assert repeated_seal == first_seal
    assert len(writer.appends) == 1
    assert writer.measurements() == projected.records
