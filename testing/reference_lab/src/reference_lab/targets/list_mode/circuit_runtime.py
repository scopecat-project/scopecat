"""Correlate list-mode result matrices to logical quantum outputs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scopecat.measurements.results import (
    MeasurementAcquisitionValue,
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementPartitionedArray,
    MeasurementUnavailable,
)
from scopecat.sdk.domain import DomainResultValue
from scopecat_quantum.program_results import (
    MappedQuantumTarget,
    QuantumTargetResultAddress,
)

from reference_lab.targets.list_mode.execution_model import (
    DigitizerResultBatch,
    ListModeRun,
)
from reference_lab.targets.list_mode.model import ListModeArtifact

_RESPONSE_UNIT = "ratio"

type _MappedListModeTarget = MappedQuantumTarget[ListModeArtifact]


@dataclass(frozen=True, slots=True)
class CorrelatedListModeRun:
    """Mapping-ordered raw target data retained as address-major arrays."""

    mapped_target: _MappedListModeTarget
    results: DigitizerResultBatch


def correlate_list_mode_run(
    mapped_target: _MappedListModeTarget,
    target_run: ListModeRun,
) -> CorrelatedListModeRun:
    """Validate and reorder one raw result batch without expanding shots."""

    artifact = mapped_target.artifact
    if target_run.artifact != artifact:
        raise ValueError("list-mode run does not retain the compiled target artifact")
    expected_addresses = mapped_target.acquisition_addresses
    if set(target_run.results.addresses) != set(expected_addresses):
        raise ValueError(
            "list-mode result rows must exactly cover every mapped acquisition"
        )
    if target_run.results.shot_count != artifact.repetitions:
        raise ValueError("list-mode result shot count does not match the artifact")
    return CorrelatedListModeRun(
        mapped_target=mapped_target,
        results=target_run.results.select(expected_addresses),
    )


def realize_measurements(
    correlated_run: CorrelatedListModeRun,
) -> Iterator[DomainResultValue[QuantumTargetResultAddress]]:
    """Project address rows directly to integrated-IQ measurement arrays."""

    row_offset = 0
    for result in correlated_run.mapped_target.mapping.results:
        row_count = len(result.result_address.acquisitions)
        row_selection: int | slice = (
            row_offset if row_count == 1 else slice(row_offset, row_offset + row_count)
        )
        yield DomainResultValue(
            result.result_address,
            realize_integrated_iq_chunks(
                tuple(
                    chunk.values[row_selection]
                    for chunk in correlated_run.results.chunks
                ),
                tuple(
                    chunk.available[row_selection]
                    for chunk in correlated_run.results.chunks
                ),
            ),
        )
        row_offset += row_count


def realize_integrated_iq_chunks(
    value_chunks: tuple[NDArray[np.complex128], ...],
    availability_chunks: tuple[NDArray[np.bool_], ...],
) -> MeasurementAcquisitionValue:
    if not any(np.any(available) for available in availability_chunks):
        first_shape = value_chunks[0].shape
        return MeasurementUnavailable.create(
            dtype="complex128",
            unit=_RESPONSE_UNIT,
            shape=(
                *first_shape[:-1],
                sum(values.shape[-1] for values in value_chunks),
            ),
            reason="missing",
            metadata={"source": "virtual-demodulator", "detail": "no lock"},
        )
    partitions = tuple(
        MeasurementArray.create(
            values=values,
            dtype="complex128",
            unit=_RESPONSE_UNIT,
            availability=(
                None
                if np.all(available)
                else MeasurementArrayAvailability.create(
                    valid=available,
                    reason="missing",
                    metadata={
                        "source": "virtual-demodulator",
                        "detail": "no lock",
                    },
                )
            ),
        )
        for values, available in zip(
            value_chunks,
            availability_chunks,
            strict=True,
        )
    )
    if len(partitions) == 1:
        return partitions[0]
    return MeasurementPartitionedArray.create(
        partitions=partitions,
        axis=partitions[0].values.ndim - 1,
        dtype="complex128",
        unit=_RESPONSE_UNIT,
    )


def realize_integrated_iq_value(
    values: NDArray[np.complex128],
    available: NDArray[np.bool_],
) -> MeasurementAcquisitionValue:
    """Realize one already contiguous value block."""

    return realize_integrated_iq_chunks((values,), (available,))


__all__ = [
    "CorrelatedListModeRun",
    "correlate_list_mode_run",
    "realize_integrated_iq_chunks",
    "realize_integrated_iq_value",
    "realize_measurements",
]
