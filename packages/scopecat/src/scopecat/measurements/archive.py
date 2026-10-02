"""Standalone current-format measurement snapshots, without project execution.

This container preserves recording data; it is not a complete run/evidence export
or an execution permission. No source code, pickle or archive extraction is used.
"""

from __future__ import annotations

import os
import tempfile
from bisect import bisect_right
from collections.abc import Iterable, Iterator
from itertools import batched
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self
from zipfile import ZIP_STORED, ZipFile

from pydantic import BaseModel, ConfigDict, Field

from scopecat.kernel.content_identity import sha256_content_hash, sha256_json_hash
from scopecat.records.content import Sha256ContentHash
from scopecat.records.measurement import MeasurementRecord
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
    measurement_dataset_content_hash,
    measurement_record_content_hash,
)

if TYPE_CHECKING:
    from scopecat.measurements.dataset import Dataset

MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_CHUNK_BYTES = 64 * 1024 * 1024


class _Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int = Field(gt=0)
    size: int = Field(gt=0, le=MAX_CHUNK_BYTES)
    digest: Sha256ContentHash


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["scopecat.measurement-snapshot.v1"] = (
        "scopecat.measurement-snapshot.v1"
    )
    header: MeasurementDatasetHeader
    chunks: tuple[_Chunk, ...]
    projection: tuple[_Chunk, ...] | None = None


class RecordSelection(BaseModel):
    """One retained logical point and its chosen physical acquisition."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    point_index: int = Field(ge=0)
    acquisition_index: int = Field(ge=0)


class _Selections(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    records: tuple[RecordSelection, ...] = Field(min_length=1, max_length=1000)


def _name(index: int) -> str:
    return f"chunks/{index:08d}.arrow"


def _check_append(
    append: MeasurementDatasetAppend,
    header: MeasurementDatasetHeader,
    offset: int,
) -> None:
    if (
        append.run_id != header.run_id
        or append.header_content_hash != header.content_hash
        or append.acquisition_start != offset
    ):
        raise ValueError("snapshot chunk does not match recording identity/order")
    if any(
        not 0 <= record.point_index < header.record_count_limit
        for record in append.records
    ):
        raise ValueError("snapshot point index exceeds recording limit")


def write_measurement_snapshot(
    destination: Path,
    header: MeasurementDatasetHeader,
    appends: Iterable[MeasurementDatasetAppend],
    *,
    projection: Iterable[RecordSelection] | None = None,
) -> None:
    """Publish a new snapshot atomically; never replace an existing user file.

    The caller supplies a consistent captured append sequence. Incomplete
    recordings remain incomplete; this does not assert successful run completion.
    """
    from scopecat.measurements.recording_arrow import (
        decode_measurement_append,
        encode_measurement_append,
    )

    with tempfile.NamedTemporaryFile(
        prefix=".measurement-", dir=destination.parent, delete=False
    ) as staging:
        staged = Path(staging.name)
    try:
        chunks: list[_Chunk] = []
        offset = 0
        with ZipFile(staged, "w", compression=ZIP_STORED) as archive:
            for index, append in enumerate(appends):
                _check_append(append, header, offset)
                content = encode_measurement_append(append, header.dataset_schema)
                # Validate the codec contract before publishing the container.
                decoded = decode_measurement_append(content, header.dataset_schema)
                _check_append(decoded, header, offset)
                chunk = _Chunk(
                    count=len(decoded.records),
                    size=len(content),
                    digest=sha256_content_hash(content),
                )
                archive.writestr(_name(index), content)
                chunks.append(chunk)
                offset += chunk.count
            selections: list[_Chunk] | None = None
            if projection is not None:
                selections = []
                previous_point = -1
                for index, batch in enumerate(batched(projection, 1000, strict=False)):
                    for selected in batch:
                        if (
                            not previous_point
                            < selected.point_index
                            < header.record_count_limit
                            or selected.acquisition_index >= offset
                        ):
                            raise ValueError("invalid recording projection order/range")
                        previous_point = selected.point_index
                    content = _Selections(records=batch).model_dump_json().encode()
                    archive.writestr(f"projection/{index:08d}.json", content)
                    selections.append(
                        _Chunk(
                            count=len(batch),
                            size=len(content),
                            digest=sha256_content_hash(content),
                        )
                    )
            manifest = _Manifest(
                header=header,
                chunks=tuple(chunks),
                projection=None if selections is None else tuple(selections),
            )
            content = manifest.model_dump_json().encode()
            if len(content) > MAX_MANIFEST_BYTES:
                raise ValueError("snapshot manifest is too large")
            archive.writestr("manifest.json", content)
        # Same-directory staging keeps publication on one filesystem. Unlike
        # replace(), link() fails if another writer or user already owns the name.
        os.link(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


class MeasurementSnapshot:
    """Read recording chunks without a server, source checkout or driver imports.

    Keep this context open while iterating. Only intersecting chunks are read;
    each selected chunk is checked and decoded in full (at most 64 MiB encoded).
    Checksums detect corruption, not publisher authenticity.
    """

    def __init__(self, path: Path | ZipFile, *, prefix: str = ""):
        # A complete exchange can share one open ZIP across recording views.
        # Prefixes address archive members, never filesystem extraction paths.
        self._owns_archive = not isinstance(path, ZipFile)
        self._archive = path if isinstance(path, ZipFile) else ZipFile(path)
        self._prefix = prefix
        try:
            info = self._archive.getinfo(prefix + "manifest.json")
            if info.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("snapshot manifest is too large")
            self._manifest = _Manifest.model_validate_json(self._archive.read(info))
            expected = {"manifest.json"} | {
                _name(index) for index in range(len(self._manifest.chunks))
            }
            expected.update(
                f"projection/{index:08d}.json"
                for index in range(len(self._manifest.projection or ()))
            )
            names = [
                name[len(prefix) :]
                for name in self._archive.namelist()
                if name.startswith(prefix)
            ]
            if len(names) != len(expected) or set(names) != expected:
                raise ValueError("snapshot has missing, duplicate or unknown members")
            for index, chunk in enumerate(self._manifest.chunks):
                if self._archive.getinfo(prefix + _name(index)).file_size != chunk.size:
                    raise ValueError("snapshot chunk size does not match manifest")
            for index, chunk in enumerate(self._manifest.projection or ()):
                if (
                    self._archive.getinfo(
                        prefix + f"projection/{index:08d}.json"
                    ).file_size
                    != chunk.size
                ):
                    raise ValueError("snapshot projection size does not match manifest")
            self._starts: list[int] = []
            position = 0
            for chunk in self._manifest.chunks:
                self._starts.append(position)
                position += chunk.count
            self._record_count = position
        except Exception:
            self.close()
            raise

    @property
    def header(self) -> MeasurementDatasetHeader:
        return self._manifest.header

    @property
    def record_count(self) -> int:
        return self._record_count

    def verify(self) -> None:
        """Check every retained acquisition and selection before accepting import."""
        for index in range(len(self._manifest.chunks)):
            self._append(index)
        if self.selected_record_count is not None:
            for _record in self.selected_records(limit=self.selected_record_count):
                pass

    def dataset(self) -> Dataset:
        """Materialize the captured analysis selection as the ordinary Dataset API.

        This explicit operation loads all selected observations into memory. For
        bounded processing use selected_records() pages instead. The result can
        outlive the archive context and supports to_xarray() and labeled variables.
        """
        from scopecat.measurements.dataset import Dataset
        from scopecat.records.content import ContentEntry
        from scopecat.records.measurement import MeasurementDataset

        count = self.selected_record_count
        if count is None:
            raise ValueError("snapshot has no captured analysis selection")
        records = tuple(self.selected_records(limit=count))
        return Dataset(
            MeasurementDataset(
                dataset_schema=self.header.dataset_schema,
                records=records,
            ),
            ContentEntry(
                role="dataset",
                id=self.header.dataset_schema.dataset_id,
                kind="measurement_dataset",
                content_hash=self._selected_content_hash(records),
                schema=self.header.dataset_schema.model_dump(mode="json"),
            ),
        )

    def _selected_content_hash(self, records: Iterable[MeasurementRecord]) -> str:
        return measurement_dataset_content_hash(
            header_content_hash=self.header.content_hash,
            record_content_hashes=tuple(
                measurement_record_content_hash(record) for record in records
            ),
        )

    @property
    def selected_content_hash(self) -> str:
        """Scientific dataset identity, distinct from the physical archive capture."""
        count = self.selected_record_count
        if count is None:
            raise ValueError("snapshot has no captured analysis selection")
        return self._selected_content_hash(self.selected_records(limit=count))

    @property
    def content_hash(self) -> str:
        """Logical identity, independent of ZIP timestamps and compression."""
        return sha256_json_hash(self._manifest.model_dump(mode="json"))

    @property
    def selected_record_count(self) -> int | None:
        if self._manifest.projection is None:
            return None
        return sum(chunk.count for chunk in self._manifest.projection)

    def _append(self, index: int) -> MeasurementDatasetAppend:
        from scopecat.measurements.recording_arrow import decode_measurement_append

        chunk = self._manifest.chunks[index]
        content = self._archive.read(self._prefix + _name(index))
        if sha256_content_hash(content) != chunk.digest:
            raise ValueError("snapshot chunk checksum mismatch")
        append = decode_measurement_append(content, self.header.dataset_schema)
        _check_append(append, self.header, self._starts[index])
        if len(append.records) != chunk.count:
            raise ValueError("snapshot chunk record count mismatch")
        return append

    def selected_records(
        self, *, offset: int = 0, limit: int = 1000
    ) -> Iterator[MeasurementRecord]:
        """Read the captured analysis selection in logical point order.

        No last-write-wins inference is made from physical history. A recording
        without an explicit captured selection cannot provide this view.
        """
        if offset < 0 or limit < 0:
            raise ValueError("snapshot offset and limit must be nonnegative")
        if self._manifest.projection is None:
            raise ValueError("snapshot has no captured analysis selection")
        if limit == 0:
            return
        position = 0
        previous_point = -1
        for index, chunk in enumerate(self._manifest.projection):
            following = position + chunk.count
            if position >= offset + limit:
                break
            if following > offset:
                content = self._archive.read(
                    self._prefix + f"projection/{index:08d}.json"
                )
                if sha256_content_hash(content) != chunk.digest:
                    raise ValueError("snapshot projection checksum mismatch")
                selected = _Selections.model_validate_json(content).records
                if len(selected) != chunk.count:
                    raise ValueError("snapshot projection count mismatch")
                for item in selected:
                    if (
                        not previous_point
                        < item.point_index
                        < self.header.record_count_limit
                        or item.acquisition_index >= self.record_count
                    ):
                        raise ValueError("invalid recording projection order/range")
                    previous_point = item.point_index
                selected = selected[
                    max(0, offset - position) : min(
                        chunk.count, offset + limit - position
                    )
                ]
                # Decode each referenced chunk once per selection page. The page
                # itself is bounded to 1000 points; do not cache the whole run.
                groups: dict[int, list[tuple[int, RecordSelection]]] = {}
                for order, item in enumerate(selected):
                    source = bisect_right(self._starts, item.acquisition_index) - 1
                    groups.setdefault(source, []).append((order, item))
                records: dict[int, MeasurementRecord] = {}
                for source, items in groups.items():
                    append = self._append(source)
                    for order, item in items:
                        record = append.records[
                            item.acquisition_index - self._starts[source]
                        ]
                        if record.point_index != item.point_index:
                            raise ValueError(
                                "snapshot projection selects another point"
                            )
                        records[order] = record
                for order in range(len(selected)):
                    yield records[order]
            position = following

    def records(
        self, *, offset: int = 0, limit: int = 1000
    ) -> Iterator[MeasurementRecord]:
        """Read by physical acquisition position, not logical point index."""
        if offset < 0 or limit < 0:
            raise ValueError("snapshot offset and limit must be nonnegative")
        if limit == 0:
            return
        position = 0
        end = offset + limit
        for index, chunk in enumerate(self._manifest.chunks):
            following = position + chunk.count
            if position >= end:
                break
            if following > offset:
                append = self._append(index)
                yield from append.records[
                    max(0, offset - position) : min(chunk.count, end - position)
                ]
            position = following

    def close(self) -> None:
        if self._owns_archive:
            self._archive.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
