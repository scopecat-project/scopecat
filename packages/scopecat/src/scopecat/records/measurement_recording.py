"""Atomic durable dataset writes and receipts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from functools import cached_property
from typing import Literal, Self, cast, override

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scopecat.kernel.content_identity import (
    content_fingerprint,
    model_wire_content_hash,
    stable_content_hash,
)
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementDatasetSchema,
    MeasurementPartitionedArray,
    MeasurementRecord,
    MeasurementSegmentedArray,
    MeasurementValue,
)

type _MeasurementDatasetRef = Literal["data/measurement_dataset/raw-measurements"]
CANONICAL_MEASUREMENT_DATASET_REF: _MeasurementDatasetRef = (
    "data/measurement_dataset/raw-measurements"
)


class _FrozenRecordingModel(BaseModel):
    """Validation-preserving copy semantics for immutable recording writes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @override
    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        if not update:
            return self
        values: dict[str, object] = {
            name: getattr(self, name) for name in type(self).model_fields
        }
        values.update(update)
        return type(self).model_validate(values)

    @override
    def __copy__(self) -> Self:
        return self

    @override
    def __deepcopy__(self, memo: dict[int, object] | None = None) -> Self:
        if memo is not None:
            memo[id(self)] = self
        return self


class MeasurementDatasetHeader(_FrozenRecordingModel):
    """Canonical schema and recording identity for one run dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    recording_contract_fingerprint: str
    dataset_schema: MeasurementDatasetSchema
    expected_record_count: int | None = Field(default=None, ge=0)
    record_count_limit: int = Field(ge=0)

    @field_validator("run_id", "recording_contract_fingerprint")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value:
            raise ValueError("measurement dataset header fields must be non-empty")
        return value

    @model_validator(mode="after")
    def validate_point_count(self) -> MeasurementDatasetHeader:
        point = next(
            dimension
            for dimension in self.dataset_schema.dimensions
            if dimension.id == "point"
        )
        if point.size != self.expected_record_count:
            raise ValueError(
                "measurement dataset header expected count must match its point "
                "dimension"
            )
        if (
            self.expected_record_count is not None
            and self.expected_record_count > self.record_count_limit
        ):
            raise ValueError("measurement expected count cannot exceed its limit")
        return self

    @property
    def operation_id(self) -> str:
        digest = stable_content_hash(
            {
                "schema": "scopecat.measurement_dataset_header_operation.v1",
                "run_id": self.run_id,
            }
        )
        return f"measurement-dataset-header:{digest}"

    @cached_property
    def _wire_content_hash(self) -> str:
        # Only the immutable header is cached, never append or mutable run state.
        return model_wire_content_hash(self)

    @property
    def content_hash(self) -> str:
        return self._wire_content_hash


class MeasurementDatasetBatch(_FrozenRecordingModel):
    """Records offered to a dataset writer in physical acquisition order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    header_content_hash: str
    records: tuple[MeasurementRecord, ...] = Field(min_length=1)

    @field_validator("run_id", "header_content_hash")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value:
            raise ValueError(
                "measurement dataset write identity fields must be non-empty"
            )
        return value

    @model_validator(mode="after")
    def validate_record_identity(self) -> MeasurementDatasetBatch:
        if any(record.run_id != self.run_id for record in self.records):
            raise ValueError("measurement dataset batch and record run ids must match")
        indices = tuple(record.point_index for record in self.records)
        if len(indices) != len(set(indices)):
            raise ValueError("measurement dataset batch point indices must be unique")
        logical_ids = tuple(record.logical_point_id for record in self.records)
        if len(logical_ids) != len(set(logical_ids)):
            raise ValueError(
                "measurement dataset batch logical point ids must be unique"
            )
        return self


class MeasurementDatasetAppend(MeasurementDatasetBatch):
    """One idempotent append to the physical acquisition log."""

    acquisition_start: int = Field(ge=0)

    @property
    def operation_id(self) -> str:
        digest = stable_content_hash(
            {
                "schema": "scopecat.measurement_dataset_append_operation.v4",
                "run_id": self.run_id,
                "header_content_hash": self.header_content_hash,
                "acquisition_start": self.acquisition_start,
            }
        )
        return f"measurement-dataset-append:{digest}"

    @property
    def content_hash(self) -> str:
        return stable_content_hash(
            {
                "schema": "scopecat.measurement_dataset_append_content.v9",
                "run_id": self.run_id,
                "header_content_hash": self.header_content_hash,
                "acquisition_start": self.acquisition_start,
                "record_content_hashes": self.record_content_hashes,
            }
        )

    @property
    def record_content_hashes(self) -> tuple[str, ...]:
        """Return chunk-neutral identities without serializing array values."""

        return tuple(measurement_record_content_hash(record) for record in self.records)


class MeasurementDatasetReceipt(_FrozenRecordingModel):
    """Durable evidence for one dataset append or seal operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    dataset_content_hash: str
    acquisition_record_count: int = Field(ge=0)
    dataset_ref: _MeasurementDatasetRef = CANONICAL_MEASUREMENT_DATASET_REF

    @field_validator("operation_id", "dataset_content_hash")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value:
            raise ValueError("measurement dataset receipt fields must be non-empty")
        return value


class MeasurementDatasetFragment(_FrozenRecordingModel):
    """Physical acquisition-log range owned by one execution segment."""

    segment_id: str
    run_id: str
    header_content_hash: str
    acquisition_start: int = Field(ge=0)
    record_count: int = Field(ge=0)
    fragment_content_hash: str
    dataset_content_hash: str | None = None

    @field_validator(
        "segment_id",
        "run_id",
        "header_content_hash",
        "fragment_content_hash",
    )
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value:
            raise ValueError("measurement fragment fields must be non-empty")
        return value

    @property
    def acquisition_end(self) -> int:
        return self.acquisition_start + self.record_count


class MeasurementDatasetSeal(_FrozenRecordingModel):
    """Seal the current fragment and request a run-level dataset identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    header_content_hash: str
    record_count: int = Field(ge=0)
    fragment_record_count: int = Field(ge=0)
    fragment_content_hash: str

    @field_validator(
        "run_id",
        "header_content_hash",
        "fragment_content_hash",
    )
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value:
            raise ValueError(
                "measurement dataset seal identity fields must be non-empty"
            )
        return value

    @property
    def operation_id(self) -> str:
        digest = stable_content_hash(
            {
                "schema": "scopecat.measurement_dataset_seal_operation.v5",
                "run_id": self.run_id,
                "header_content_hash": self.header_content_hash,
            }
        )
        return f"measurement-dataset-seal:{digest}"

    @property
    def content_hash(self) -> str:
        return model_wire_content_hash(self)


def measurement_dataset_content_hash(
    *,
    header_content_hash: str,
    record_content_hashes: tuple[str, ...],
) -> str:
    """Hash a header and ordered records, independent of IPC chunk boundaries."""

    return stable_content_hash(
        {
            "schema": "scopecat.measurement_dataset_content.v3",
            "header_content_hash": header_content_hash,
            "record_content_hashes": record_content_hashes,
        }
    )


def measurement_fragment_content_hash(
    *,
    header_content_hash: str,
    record_content_hashes: tuple[str, ...],
) -> str:
    """Identify one segment's acquisition sequence independently of chunking."""

    return stable_content_hash(
        {
            "schema": "scopecat.measurement_dataset_fragment_content.v2",
            "header_content_hash": header_content_hash,
            "record_content_hashes": record_content_hashes,
        }
    )


def measurement_record_content_hash(record: MeasurementRecord) -> str:
    """Identify one normalized record using buffer fingerprints for arrays."""

    return stable_content_hash(
        {
            "schema": "scopecat.measurement_record_content.v2",
            "record": content_fingerprint(
                {
                    "run_id": record.run_id,
                    "logical_point_id": record.logical_point_id,
                    "point_index": record.point_index,
                    "coordinates": {
                        key: _measurement_value_content_identity(value)
                        for key, value in record.coordinates.items()
                    },
                    "observables": {
                        key: _measurement_value_content_identity(value)
                        for key, value in record.observables.items()
                    },
                    "acquisition_evidence": record.acquisition_evidence,
                    "metadata": record.metadata,
                }
            ),
        }
    )


def _measurement_value_content_identity(value: MeasurementValue) -> object:
    if isinstance(value, MeasurementArray | MeasurementPartitionedArray):
        return _rectangular_array_content_identity(value)
    if isinstance(value, MeasurementSegmentedArray):
        return {
            "kind": value.kind,
            "dtype": value.dtype,
            "unit": value.unit,
            "segments": tuple(
                _measurement_value_content_identity(segment)
                for segment in value.segments
            ),
            "metadata": value.metadata,
        }
    return value


def _rectangular_array_content_identity(
    value: MeasurementArray | MeasurementPartitionedArray,
) -> object:
    availability = value.availability
    return {
        "kind": "rectangular_array",
        "dtype": value.dtype,
        "unit": value.unit,
        "shape": value.shape,
        "values_sha256": _rectangular_array_sha256(value),
        "availability": (
            None
            if availability is None
            else {
                "valid_sha256": hashlib.sha256(
                    memoryview(availability.valid).cast("B")
                ).hexdigest(),
                "unavailable": availability.unavailable,
            }
        ),
        "metadata": value.metadata,
    }


def _rectangular_array_sha256(
    value: MeasurementArray | MeasurementPartitionedArray,
) -> str:
    availability = value.availability
    valid = None if availability is None else availability.valid
    string_width: int | None = None
    # Arrow nulls also discard Unicode padding determined by masked fillers.
    if value.dtype == "string" and valid is not None:
        arrays = (value,) if isinstance(value, MeasurementArray) else value.partitions
        string_width = max(
            (
                len(str(item))
                for array in arrays
                for item in (
                    array.values.ravel()
                    if array.availability is None
                    else array.values[array.availability.valid]
                )
            ),
            default=1,
        )
        string_width = max(1, string_width)
    if isinstance(value, MeasurementArray):
        return hashlib.sha256(
            _canonical_array_bytes(value.values, valid, string_width)
        ).hexdigest()
    digest = hashlib.sha256()
    prefix_shape = value.shape[: value.axis]
    prefixes = np.ndindex(prefix_shape) if prefix_shape else ((),)
    for prefix in prefixes:
        offset = 0
        for partition in value.partitions:
            selected = cast("NDArray[np.generic]", partition.values[prefix])
            size = partition.shape[value.axis]
            mask = (
                None
                if valid is None
                else valid[(*prefix, slice(offset, offset + size))]
            )
            digest.update(_canonical_array_bytes(selected, mask, string_width))
            offset += size
    return digest.hexdigest()


def _canonical_array_bytes(
    values: NDArray[np.generic],
    valid: NDArray[np.bool_] | None,
    string_width: int | None = None,
) -> memoryview:
    """Hash Arrow's decoded fill values without mutating acquisition buffers."""
    if valid is not None and not bool(np.all(valid)):
        values = values.copy()
        values[~valid] = "" if values.dtype.kind == "U" else 0
    if string_width is not None:
        values = values.astype(f"U{string_width}")
    return memoryview(values).cast("B")


__all__ = [
    "CANONICAL_MEASUREMENT_DATASET_REF",
    "MeasurementDatasetAppend",
    "MeasurementDatasetBatch",
    "MeasurementDatasetFragment",
    "MeasurementDatasetHeader",
    "MeasurementDatasetReceipt",
    "MeasurementDatasetSeal",
    "measurement_dataset_content_hash",
    "measurement_fragment_content_hash",
    "measurement_record_content_hash",
]
