import pytest

from scopecat.measurements.previews import (
    MeasurementArraySummary,
    preview_measurement_records,
)
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementPartitionedArray,
    MeasurementRecord,
    MeasurementSegmentedArray,
    MeasurementUnavailable,
)


def test_array_budget_is_shared_by_the_page_and_preserves_source_values():
    value = MeasurementArray.create(values=[1.0, 2.0, 3.0])
    records = tuple(
        MeasurementRecord(
            run_id="run", point_index=i, coordinates={}, observables={"signal": value}
        )
        for i in range(2)
    )
    previews = preview_measurement_records(records, array_sample_budget=3)
    assert isinstance(previews[0].observables["signal"], MeasurementArray)
    assert isinstance(previews[1].observables["signal"], MeasurementArraySummary)
    assert value.values.tolist() == [1.0, 2.0, 3.0]
    assert all(record.observables["signal"] is value for record in records)


def test_partition_summary_does_not_join_buffers_and_retains_availability(
    monkeypatch: pytest.MonkeyPatch,
):
    def unexpected(*args: object):
        raise AssertionError("preview joined partition buffers")

    monkeypatch.setattr(MeasurementPartitionedArray, "materialize", unexpected)
    value = MeasurementPartitionedArray.create(
        axis=0,
        partitions=(
            MeasurementArray.create(values=[1.0, 2.0]),
            MeasurementArray.create(
                values=[0.0, 4.0],
                availability=MeasurementArrayAvailability.create(
                    valid=[False, True], reason="overload"
                ),
            ),
        ),
    )
    record = MeasurementRecord(
        run_id="run", point_index=0, coordinates={}, observables={"signal": value}
    )
    summary = preview_measurement_records((record,), array_sample_budget=0)[
        0
    ].observables["signal"]
    assert isinstance(summary, MeasurementArraySummary)
    assert summary.shape == (4,)
    assert summary.sample_count == 4
    assert summary.available_sample_count == 3
    assert summary.unavailable_reasons == ("overload",)


def test_segment_summary_does_not_invent_the_length_of_missing_data():
    value = MeasurementSegmentedArray.create(
        segments=(
            MeasurementArray.create(values=[1.0, 2.0]),
            MeasurementUnavailable.create(
                dtype="float64", unit=None, shape=(None,), reason="missing", metadata={}
            ),
        )
    )
    record = MeasurementRecord(
        run_id="run", point_index=0, coordinates={}, observables={"signal": value}
    )
    summary = preview_measurement_records((record,))[0].observables["signal"]
    assert isinstance(summary, MeasurementArraySummary)
    assert summary.shape == (2, None)
    assert summary.sample_count is None
    assert summary.available_sample_count == 2
    assert summary.unavailable_reasons == ("missing",)
