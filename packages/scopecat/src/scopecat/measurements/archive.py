"""Standalone current-format measurement snapshots, without project execution.

This container preserves recording data; it is not a complete run/evidence export
or an execution permission. No source code, pickle or archive extraction is used.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal, Self
from zipfile import ZIP_STORED, ZipFile

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.kernel.content_identity import sha256_content_hash
from scopecat.measurements.recording_arrow import (
    decode_measurement_append,
    encode_measurement_append,
)
from scopecat.records.content import Sha256ContentHash
from scopecat.records.measurement import MeasurementRecord
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
)

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

    @model_validator(mode="after")
    def validate_count(self) -> _Manifest:
        if sum(chunk.count for chunk in self.chunks) > self.header.record_count_limit:
            raise ValueError("snapshot exceeds recording count limit")
        return self


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


def write_measurement_snapshot(
    destination: Path,
    header: MeasurementDatasetHeader,
    appends: Iterable[MeasurementDatasetAppend],
) -> None:
    """Publish a new snapshot atomically; never replace an existing user file.

    The caller supplies a consistent captured append sequence. Incomplete
    recordings remain incomplete; this does not assert successful run completion.
    """
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
            manifest = _Manifest(header=header, chunks=tuple(chunks))
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

    def __init__(self, path: Path):
        self._archive = ZipFile(path)
        try:
            info = self._archive.getinfo("manifest.json")
            if info.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("snapshot manifest is too large")
            self._manifest = _Manifest.model_validate_json(self._archive.read(info))
            expected = {"manifest.json"} | {
                _name(index) for index in range(len(self._manifest.chunks))
            }
            names = self._archive.namelist()
            if len(names) != len(expected) or set(names) != expected:
                raise ValueError("snapshot has missing, duplicate or unknown members")
            for index, chunk in enumerate(self._manifest.chunks):
                if self._archive.getinfo(_name(index)).file_size != chunk.size:
                    raise ValueError("snapshot chunk size does not match manifest")
        except Exception:
            self._archive.close()
            raise

    @property
    def header(self) -> MeasurementDatasetHeader:
        return self._manifest.header

    @property
    def record_count(self) -> int:
        return sum(chunk.count for chunk in self._manifest.chunks)

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
                content = self._archive.read(_name(index))
                if sha256_content_hash(content) != chunk.digest:
                    raise ValueError("snapshot chunk checksum mismatch")
                append = decode_measurement_append(content, self.header.dataset_schema)
                _check_append(append, self.header, position)
                if len(append.records) != chunk.count:
                    raise ValueError("snapshot chunk record count mismatch")
                yield from append.records[
                    max(0, offset - position) : min(chunk.count, end - position)
                ]
            position = following

    def close(self) -> None:
        self._archive.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
