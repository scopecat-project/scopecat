from __future__ import annotations

from scopecat_quantum._ids import AcquisitionSlotId, TargetCompileEntryId
from scopecat_quantum.targets import TargetAcquisitionAddress

from reference_lab.targets.list_mode.execution_model import (
    DigitizerResultBatch,
    DigitizerResultChunk,
)

from ._list_mode_test_support import (
    MeasurementArray,
    MeasurementPartitionedArray,
    np,
    realize_integrated_iq_chunks,
    realize_integrated_iq_value,
)


def test_result_batch_reuses_canonical_and_contiguous_row_storage() -> None:
    entry_id = TargetCompileEntryId("entry")
    addresses = tuple(
        TargetAcquisitionAddress(entry_id, AcquisitionSlotId(f"slot-{index}"))
        for index in range(3)
    )
    values = np.arange(12, dtype=np.float64).astype(np.complex128).reshape(3, 4)
    available = np.ones((3, 4), dtype=np.bool_)
    batch = DigitizerResultBatch(
        addresses=addresses,
        shot_count=4,
        chunks=(DigitizerResultChunk(0, values, available),),
    )

    assert batch.select(addresses) is batch
    contiguous = batch.select(addresses[1:])
    assert np.shares_memory(contiguous.chunks[0].values, values)
    assert np.shares_memory(contiguous.chunks[0].available, available)

    reordered = batch.select((addresses[2], addresses[0]))
    assert not np.shares_memory(reordered.chunks[0].values, values)


def test_list_mode_logical_result_preserves_partial_shot_availability() -> None:
    value = realize_integrated_iq_value(
        np.asarray([1 + 2j, 0j, 3 + 4j], dtype=np.complex128),
        np.asarray([True, False, True], dtype=np.bool_),
    )

    assert isinstance(value, MeasurementArray)
    assert value.availability is not None
    assert value.availability.valid.tolist() == [True, False, True]
    [failure] = value.availability.unavailable
    assert failure.reason == "missing"
    assert failure.flat_indices == (1,)


def test_list_mode_logical_result_preserves_entity_by_shot_shape() -> None:
    value = realize_integrated_iq_value(
        np.asarray(
            [[1 + 2j, 3 + 4j], [5 + 6j, 0j]],
            dtype=np.complex128,
        ),
        np.asarray([[True, True], [True, False]], dtype=np.bool_),
    )

    assert isinstance(value, MeasurementArray)
    assert value.values.shape == (2, 2)
    assert value.availability is not None
    assert value.availability.valid.tolist() == [[True, True], [True, False]]


def test_list_mode_logical_result_preserves_shot_partitions() -> None:
    value = realize_integrated_iq_chunks(
        (
            np.asarray([1 + 2j, 3 + 4j], dtype=np.complex128),
            np.asarray([0j, 5 + 6j, 7 + 8j], dtype=np.complex128),
        ),
        (
            np.asarray([True, True], dtype=np.bool_),
            np.asarray([False, True, True], dtype=np.bool_),
        ),
    )

    assert isinstance(value, MeasurementPartitionedArray)
    assert value.axis == 0
    assert [partition.shape for partition in value.partitions] == [(2,), (3,)]
    assert value.shape == (5,)
    assert value.availability is not None
    assert value.availability.valid.tolist() == [True, True, False, True, True]
