"""Array-native execution results and identities for the list-mode target."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, cast

import numpy as np
from numpy.typing import NDArray
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat_quantum._ids import TargetCompileEntryId
from scopecat_quantum.targets import TargetAcquisitionAddress

from reference_lab.targets.list_mode.model import (
    DigitizerAcquisitionWindow,
    ListModeArtifact,
    acquisition_slot_identity_payload,
)


@dataclass(frozen=True, slots=True)
class DigitizerValueBlock:
    """One acquisition address across a contiguous vector of shots."""

    values: NDArray[np.complex128]
    available: NDArray[np.bool_]

    def __post_init__(self) -> None:
        values, available = _normalized_arrays(self.values, self.available, ndim=1)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "available", available)


@dataclass(frozen=True, slots=True)
class DigitizerResultChunk:
    """Address-major integrated-IQ values for one contiguous shot slice."""

    shot_start: int
    values: NDArray[np.complex128]
    available: NDArray[np.bool_]

    def __post_init__(self) -> None:
        values, available = _normalized_arrays(self.values, self.available, ndim=2)
        if self.shot_start < 0:
            raise ValueError("digitizer result chunk start must be non-negative")
        if values.shape[1] == 0:
            raise ValueError("digitizer result chunks must contain at least one shot")
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "available", available)

    @property
    def shot_count(self) -> int:
        return cast("int", self.values.shape[1])

    @property
    def shot_stop(self) -> int:
        return self.shot_start + self.shot_count


@dataclass(frozen=True, slots=True)
class DigitizerResultBatch:
    """Chunked address-major integrated-IQ results across all shots."""

    addresses: tuple[TargetAcquisitionAddress, ...]
    shot_count: int
    chunks: tuple[DigitizerResultChunk, ...]

    def __post_init__(self) -> None:
        if len(self.addresses) != len(set(self.addresses)):
            raise ValueError("digitizer result addresses must be unique")
        if self.shot_count <= 0:
            raise ValueError("digitizer result shot count must be positive")
        chunks = tuple(self.chunks)
        expected_start = 0
        for chunk in chunks:
            if chunk.values.shape[0] != len(self.addresses):
                raise ValueError(
                    "digitizer result chunk rows must match acquisition addresses"
                )
            if chunk.shot_start != expected_start:
                raise ValueError("digitizer result chunks must exactly partition shots")
            expected_start = chunk.shot_stop
        if expected_start != self.shot_count:
            raise ValueError("digitizer result chunks must exactly cover all shots")
        object.__setattr__(self, "addresses", tuple(self.addresses))
        object.__setattr__(self, "chunks", chunks)

    @property
    def result_count(self) -> int:
        """Return the number of address-qualified shot results."""

        return len(self.addresses) * self.shot_count

    def select(
        self,
        addresses: tuple[TargetAcquisitionAddress, ...],
    ) -> DigitizerResultBatch:
        """Select and reorder address rows without constructing shot objects.

        Canonical and contiguous selections retain the device-owned arrays as
        views. Only a genuinely non-contiguous reorder allocates row copies.
        """

        if addresses == self.addresses:
            return self

        row_by_address = {
            address: row_index for row_index, address in enumerate(self.addresses)
        }
        rows = tuple(row_by_address[address] for address in addresses)
        row_selection: slice | list[int]
        if not rows:
            row_selection = slice(0, 0)
        elif rows == tuple(range(rows[0], rows[0] + len(rows))):
            row_selection = slice(rows[0], rows[0] + len(rows))
        else:
            row_selection = list(rows)
        return DigitizerResultBatch(
            addresses=addresses,
            shot_count=self.shot_count,
            chunks=tuple(
                DigitizerResultChunk(
                    shot_start=chunk.shot_start,
                    values=chunk.values[row_selection],
                    available=chunk.available[row_selection],
                )
                for chunk in self.chunks
            ),
        )


class AcquisitionResponse(Protocol):
    """Pluggable deterministic vector response for virtual acquisitions."""

    @property
    def fingerprint(self) -> str:
        """Return the stable identity of this response behavior."""
        ...

    def values_for(
        self,
        *,
        entry_id: TargetCompileEntryId,
        window: DigitizerAcquisitionWindow,
        shot_indices: NDArray[np.int64],
    ) -> DigitizerValueBlock:
        """Return one address's values for a contiguous shot vector."""
        ...


@dataclass(frozen=True, slots=True)
class ListModeRun:
    """Immutable result of one complete list-mode execution."""

    results: DigitizerResultBatch
    artifact: ListModeArtifact
    fingerprint: str


def digitizer_addresses(
    artifact: ListModeArtifact,
) -> tuple[TargetAcquisitionAddress, ...]:
    """Return canonical address rows in target entry/window order."""

    return tuple(
        TargetAcquisitionAddress(entry_id=entry.entry_id, slot_id=window.slot_id)
        for entry in artifact.entries
        for window in entry.acquisitions
    )


def run_fingerprint(
    *,
    artifact: ListModeArtifact,
    results: DigitizerResultBatch,
    response_fingerprint: str,
) -> str:
    """Identify one array-native raw result without per-shot JSON expansion."""

    return sha256_json_hash(
        {
            "schema": "reference_lab.virtual_list_mode_run.v4",
            "artifact_id": artifact.id.value,
            "artifact_fingerprint": artifact.artifact_fingerprint,
            "response_fingerprint": response_fingerprint,
            "addresses": [
                {
                    "entry_id": address.entry_id.value,
                    "slot_id": acquisition_slot_identity_payload(address.slot_id),
                }
                for address in results.addresses
            ],
            "shape": [len(results.addresses), results.shot_count],
            "chunks": [
                {
                    "shot_start": chunk.shot_start,
                    "shot_count": chunk.shot_count,
                    "values_sha256": hashlib.sha256(
                        memoryview(chunk.values).cast("B")
                    ).hexdigest(),
                    "available_sha256": hashlib.sha256(
                        memoryview(chunk.available).cast("B")
                    ).hexdigest(),
                }
                for chunk in results.chunks
            ],
        }
    )


def _normalized_arrays(
    values: object,
    available: object,
    *,
    ndim: int,
) -> tuple[NDArray[np.complex128], NDArray[np.bool_]]:
    selected_values = np.ascontiguousarray(values, dtype=np.complex128)
    selected_available = np.ascontiguousarray(available, dtype=np.bool_)
    if (
        selected_values.ndim != ndim
        or selected_values.shape != selected_available.shape
    ):
        raise ValueError("digitizer values and availability must have matching shape")
    if not np.all(selected_available):
        selected_values = selected_values.copy()
        selected_values[~selected_available] = 0j
    selected_values.flags.writeable = False
    selected_available.flags.writeable = False
    return selected_values, selected_available


__all__ = [
    "AcquisitionResponse",
    "DigitizerResultBatch",
    "DigitizerResultChunk",
    "DigitizerValueBlock",
    "ListModeRun",
    "digitizer_addresses",
    "run_fingerprint",
]
