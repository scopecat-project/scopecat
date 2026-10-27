"""Daemon-owned live measurement state and durable batching policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from scopecat.daemon.views import MeasurementLivePreview
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementPartitionedArray,
    MeasurementRecord,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
)

MEASUREMENT_CHUNK_RECORD_LIMIT = 256
MEASUREMENT_CHUNK_VALUE_BYTE_LIMIT = 8 * 1024 * 1024


class ActiveMeasurementConflict(ValueError):
    """Incoming live records do not extend the daemon-owned active prefix."""


@dataclass(slots=True)
class _ActiveMeasurementDataset:
    segment_id: str
    header: MeasurementDatasetHeader
    received_record_count: int = 0
    durable_record_count: int = 0
    pending: list[MeasurementRecord] = field(default_factory=list)
    pending_value_bytes: int = 0
    latest: MeasurementRecord | None = None


class ActiveMeasurementStore:
    """Retain one bounded pending prefix and latest point for every active run."""

    def __init__(
        self,
        *,
        record_limit: int = MEASUREMENT_CHUNK_RECORD_LIMIT,
        value_byte_limit: int = MEASUREMENT_CHUNK_VALUE_BYTE_LIMIT,
    ) -> None:
        self._record_limit = record_limit
        self._value_byte_limit = value_byte_limit
        self._datasets: dict[str, _ActiveMeasurementDataset] = {}
        self._lock = Lock()

    def initialize(
        self,
        header: MeasurementDatasetHeader,
        *,
        segment_id: str,
        acquisition_start: int,
    ) -> None:
        with self._lock:
            current = self._datasets.get(header.run_id)
            if current is None or current.segment_id != segment_id:
                self._datasets[header.run_id] = _ActiveMeasurementDataset(
                    segment_id=segment_id,
                    header=header,
                    received_record_count=acquisition_start,
                    durable_record_count=acquisition_start,
                )
                return
            if current.header.content_hash != header.content_hash:
                raise ActiveMeasurementConflict(
                    "active measurement header already has different content"
                )

    def ingest(self, batch: MeasurementDatasetAppend) -> None:
        with self._lock:
            active = self._require(batch.run_id)
            if batch.header_content_hash != active.header.content_hash:
                raise ActiveMeasurementConflict(
                    "measurement ingest references a different active header"
                )
            if batch.acquisition_start != active.received_record_count:
                raise ActiveMeasurementConflict(
                    "measurement ingest is not the next acquisition-log range"
                )
            active.pending.extend(batch.records)
            active.pending_value_bytes += sum(
                _measurement_record_value_bytes(record) for record in batch.records
            )
            active.received_record_count += len(batch.records)
            active.latest = batch.records[-1]

    def next_chunk(
        self,
        run_id: str,
        *,
        force: bool,
    ) -> tuple[MeasurementRecord, ...]:
        with self._lock:
            active = self._require(run_id)
            if not active.pending:
                return ()
            if not force and (
                len(active.pending) < self._record_limit
                and active.pending_value_bytes < self._value_byte_limit
            ):
                return ()
            selected: list[MeasurementRecord] = []
            selected_bytes = 0
            for record in active.pending:
                record_bytes = _measurement_record_value_bytes(record)
                if selected and selected_bytes + record_bytes > self._value_byte_limit:
                    break
                selected.append(record)
                selected_bytes += record_bytes
                if (
                    len(selected) >= self._record_limit
                    or selected_bytes >= self._value_byte_limit
                ):
                    break
            return tuple(selected)

    def commit_chunk(self, run_id: str, records: tuple[MeasurementRecord, ...]) -> None:
        with self._lock:
            active = self._require(run_id)
            if tuple(active.pending[: len(records)]) != records:
                raise ActiveMeasurementConflict(
                    "durable measurement chunk is not the active pending prefix"
                )
            del active.pending[: len(records)]
            active.pending_value_bytes -= sum(
                _measurement_record_value_bytes(record) for record in records
            )
            active.durable_record_count += len(records)

    def preview(
        self,
        run_id: str,
        *,
        after_record_count: int | None = None,
    ) -> MeasurementLivePreview:
        return self.snapshot(
            run_id,
            after_record_count=after_record_count,
        )[0]

    def snapshot(
        self,
        run_id: str,
        *,
        after_record_count: int | None = None,
    ) -> tuple[MeasurementLivePreview, MeasurementDatasetHeader | None]:
        """Read live counters, latest record, and its schema atomically."""

        with self._lock:
            active = self._datasets.get(run_id)
            if active is None:
                return MeasurementLivePreview(), None
            return (
                MeasurementLivePreview(
                    active=True,
                    latest=(
                        active.latest
                        if after_record_count is None
                        or active.received_record_count > after_record_count
                        else None
                    ),
                    received_record_count=active.received_record_count,
                    durable_record_count=active.durable_record_count,
                ),
                active.header,
            )

    def header_content_hash(self, run_id: str) -> str:
        with self._lock:
            return self._require(run_id).header.content_hash

    def segment_id(self, run_id: str) -> str:
        with self._lock:
            return self._require(run_id).segment_id

    def durable_record_count(self, run_id: str) -> int:
        with self._lock:
            return self._require(run_id).durable_record_count

    def clear(self, run_id: str) -> None:
        with self._lock:
            self._datasets.pop(run_id, None)

    def run_ids(self) -> tuple[str, ...]:
        """Return a stable snapshot of runs retaining volatile measurement state."""

        with self._lock:
            return tuple(self._datasets)

    def clear_all(self) -> None:
        """Release every process-local measurement dataset."""

        with self._lock:
            self._datasets.clear()

    def _require(self, run_id: str) -> _ActiveMeasurementDataset:
        try:
            return self._datasets[run_id]
        except KeyError as error:
            raise ActiveMeasurementConflict(
                "measurement ingest requires an active dataset header"
            ) from error


def _measurement_record_value_bytes(record: MeasurementRecord) -> int:
    return sum(
        (
            value.values.nbytes
            if isinstance(value, MeasurementArray)
            else value.value_nbytes
        )
        for values in (record.coordinates, record.observables)
        for value in values.values()
        if isinstance(value, MeasurementArray | MeasurementPartitionedArray)
    )


__all__ = [
    "MEASUREMENT_CHUNK_RECORD_LIMIT",
    "MEASUREMENT_CHUNK_VALUE_BYTE_LIMIT",
    "ActiveMeasurementConflict",
    "ActiveMeasurementStore",
]
