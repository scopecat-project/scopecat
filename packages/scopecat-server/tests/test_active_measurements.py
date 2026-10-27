from __future__ import annotations

import numpy as np
import pytest
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementPointDomainAxis,
    MeasurementPointDomainValuesSource,
    MeasurementProductGridPointDomain,
    MeasurementRecord,
    MeasurementScalar,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
)

from scopecat_server.services.active_measurements import (
    ActiveMeasurementConflict,
    ActiveMeasurementStore,
)


def test_active_measurements_expose_latest_before_bounded_flush() -> None:
    store = ActiveMeasurementStore(record_limit=3)
    header = _header(point_count=7)
    store.initialize(header, segment_id="segment-1", acquisition_start=0)

    first = _append(header, tuple(_record(index) for index in range(2)))
    store.ingest(first)

    assert store.next_chunk("run-1", force=False) == ()
    assert store.preview("run-1").latest == _record(1)
    assert store.preview("run-1", after_record_count=2).latest is None
    assert store.preview("run-1").received_record_count == 2
    assert store.preview("run-1").durable_record_count == 0

    store.ingest(_append(header, tuple(_record(index) for index in range(2, 6))))
    first_chunk = store.next_chunk("run-1", force=False)
    assert tuple(record.point_index for record in first_chunk) == (0, 1, 2)
    store.commit_chunk("run-1", first_chunk)
    second_chunk = store.next_chunk("run-1", force=False)
    assert tuple(record.point_index for record in second_chunk) == (3, 4, 5)


def test_active_measurements_bound_large_waveforms_by_array_bytes() -> None:
    store = ActiveMeasurementStore(record_limit=100, value_byte_limit=24)
    header = _header(point_count=3)
    store.initialize(header, segment_id="segment-1", acquisition_start=0)
    records = tuple(_record(index, waveform=True) for index in range(3))
    store.ingest(_append(header, records))

    chunk = store.next_chunk("run-1", force=False)
    assert chunk == (records[0],)
    store.commit_chunk("run-1", chunk)
    assert store.next_chunk("run-1", force=False) == (records[1],)


def test_active_measurements_reject_nonsequential_acquisition_ingest() -> None:
    store = ActiveMeasurementStore()
    header = _header(point_count=2)
    store.initialize(header, segment_id="segment-1", acquisition_start=0)

    with pytest.raises(ActiveMeasurementConflict, match="acquisition-log"):
        store.ingest(
            MeasurementDatasetAppend(
                run_id="run-1",
                header_content_hash=header.content_hash,
                acquisition_start=1,
                records=(_record(1),),
            )
        )


def test_new_segment_replaces_volatile_state_at_its_durable_prefix() -> None:
    store = ActiveMeasurementStore()
    header = _header(point_count=3)
    store.initialize(header, segment_id="segment-1", acquisition_start=0)
    store.ingest(_append(header, (_record(0),)))

    store.initialize(header, segment_id="segment-2", acquisition_start=1)

    preview = store.preview("run-1")
    assert store.segment_id("run-1") == "segment-2"
    assert preview.received_record_count == 1
    assert preview.durable_record_count == 1
    assert preview.latest is None


def _header(*, point_count: int) -> MeasurementDatasetHeader:
    return MeasurementDatasetHeader(
        run_id="run-1",
        recording_contract_fingerprint="contract-1",
        dataset_schema=MeasurementDatasetSchema(
            dataset_id="raw-measurements",
            point_domain=MeasurementProductGridPointDomain(
                axes=[
                    MeasurementPointDomainAxis(
                        id="point",
                        size=point_count,
                        source=MeasurementPointDomainValuesSource(
                            values=[
                                MeasurementScalar.create(
                                    dtype="int64",
                                    value=index,
                                )
                                for index in range(point_count)
                            ]
                        ),
                    )
                ]
            ),
            dimensions=[
                MeasurementDimension(id="point", kind="point", size=point_count)
            ],
        ),
        expected_record_count=point_count,
        record_count_limit=point_count,
    )


def _append(
    header: MeasurementDatasetHeader,
    records: tuple[MeasurementRecord, ...],
) -> MeasurementDatasetAppend:
    return MeasurementDatasetAppend(
        run_id="run-1",
        header_content_hash=header.content_hash,
        acquisition_start=records[0].point_index,
        records=records,
    )


def _record(point_index: int, *, waveform: bool = False) -> MeasurementRecord:
    return MeasurementRecord(
        run_id="run-1",
        logical_point_id=f"point-{point_index}",
        point_index=point_index,
        coordinates={},
        observables=(
            {"waveform": MeasurementArray.create(values=np.arange(2, dtype=np.float64))}
            if waveform
            else {}
        ),
    )
