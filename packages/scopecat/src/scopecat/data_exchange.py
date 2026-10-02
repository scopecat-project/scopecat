"""Read scientific evidence and recording partitions without an application."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self
from zipfile import ZIP_STORED, ZipFile

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.kernel.content_identity import sha256_content_hash, sha256_json_hash
from scopecat.measurements.archive import MeasurementSnapshot
from scopecat.records.content import Sha256ContentHash
from scopecat.records.exchange import ScientificEvidence

MAX_INDEX_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024


class PayloadReference(BaseModel):
    """An inert logical reference to bytes; no archive path comes from the ref."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_kind: Literal["run", "analysis"]
    owner_id: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    digest: Sha256ContentHash
    size: int = Field(ge=0)


@dataclass(frozen=True)
class PayloadSource:
    reference: PayloadReference
    path: Path


class _Index(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["scopecat.scientific-exchange.v1"] = (
        "scopecat.scientific-exchange.v1"
    )
    evidence_digest: Sha256ContentHash
    evidence_size: int = Field(ge=1, le=MAX_EVIDENCE_BYTES)
    recordings: dict[str, Sha256ContentHash]
    payloads: tuple[PayloadReference, ...]

    @model_validator(mode="after")
    def validate_payloads(self) -> Self:
        identities = {
            (item.owner_kind, item.owner_id, item.ref) for item in self.payloads
        }
        if len(identities) != len(self.payloads):
            raise ValueError("exchange contains duplicate payload references")
        sizes: dict[str, int] = {}
        for item in self.payloads:
            if item.digest in sizes and sizes[item.digest] != item.size:
                raise ValueError("exchange payload size identities disagree")
            sizes[item.digest] = item.size
        return self


def _recording_prefix(run_id: str) -> str:
    return f"recordings/{hashlib.sha256(run_id.encode()).hexdigest()}/"


def _object_name(digest: str) -> str:
    return f"objects/{digest.removeprefix('sha256:')}"


def write_scientific_exchange(
    destination: Path,
    evidence: ScientificEvidence,
    recordings: Mapping[str, Path],
    payloads: Iterable[PayloadSource] = (),
) -> None:
    """Publish an owned evidence capture atomically without overwriting user data.

    The caller captures all sources at one storage boundary. This writer checks
    bytes while copying and never executes or extracts retained code. Scientific
    dependency closure must be resolved by the capture layer before publication.
    """
    encoded = evidence.model_dump_json().encode()
    if len(encoded) > MAX_EVIDENCE_BYTES:
        raise ValueError("exchange evidence document exceeds the metadata budget")
    run_ids = {item.snapshot.run_id for item in evidence.runs}
    if not set(recordings) <= run_ids:
        raise ValueError("exchange recording has no run evidence")
    with tempfile.NamedTemporaryFile(
        prefix=".exchange-", dir=destination.parent, delete=False
    ) as temp:
        staged = Path(temp.name)
    try:
        recording_hashes: dict[str, str] = {}
        references: list[PayloadReference] = []
        copied: set[str] = set()
        with ZipFile(staged, "w", compression=ZIP_STORED, allowZip64=True) as output:
            output.writestr("evidence.json", encoded)
            for run_id, path in sorted(recordings.items()):
                with ZipFile(path) as source:
                    with MeasurementSnapshot(source) as snapshot:
                        snapshot.verify()
                        if snapshot.header.run_id != run_id:
                            raise ValueError("exchange recording identity differs")
                        recording_hashes[run_id] = snapshot.content_hash
                    for name in source.namelist():
                        with (
                            source.open(name) as src,
                            output.open(
                                _recording_prefix(run_id) + name, "w", force_zip64=True
                            ) as dst,
                        ):
                            shutil.copyfileobj(src, dst, length=1024 * 1024)
            for payload in payloads:
                reference = payload.reference
                references.append(reference)
                if reference.digest in copied:
                    continue
                digest = hashlib.sha256()
                size = 0
                with (
                    payload.path.open("rb") as src,
                    output.open(
                        _object_name(reference.digest), "w", force_zip64=True
                    ) as dst,
                ):
                    while block := src.read(1024 * 1024):
                        dst.write(block)
                        digest.update(block)
                        size += len(block)
                if (
                    size != reference.size
                    or f"sha256:{digest.hexdigest()}" != reference.digest
                ):
                    raise ValueError("exchange payload changed or is corrupt")
                copied.add(reference.digest)
            index = _Index(
                evidence_digest=sha256_content_hash(encoded),
                evidence_size=len(encoded),
                recordings=recording_hashes,
                payloads=tuple(
                    sorted(
                        references,
                        key=lambda ref: (ref.owner_kind, ref.owner_id, ref.ref),
                    )
                ),
            )
            index_bytes = index.model_dump_json().encode()
            if len(index_bytes) > MAX_INDEX_BYTES:
                raise ValueError("exchange index exceeds the metadata budget")
            output.writestr("index.json", index_bytes)
        # Recheck the owned bytes: a source recording can change during copying.
        with ScientificExchange(staged) as captured:
            captured.verify()
        os.link(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


class ScientificExchange:
    """One read-only evidence package; child recordings share its lifetime."""

    def __init__(self, path: Path):
        self._archive = ZipFile(path)
        try:
            if self._archive.getinfo("index.json").file_size > MAX_INDEX_BYTES:
                raise ValueError("exchange index exceeds the metadata budget")
            self._index = _Index.model_validate_json(self._archive.read("index.json"))
            if (
                self._archive.getinfo("evidence.json").file_size
                != self._index.evidence_size
            ):
                raise ValueError("exchange evidence size differs")
            content = self._archive.read("evidence.json")
            if sha256_content_hash(content) != self._index.evidence_digest:
                raise ValueError("exchange evidence checksum differs")
            self.evidence = ScientificEvidence.model_validate_json(content)
            self._recordings: dict[str, MeasurementSnapshot] = {}
            expected = {"index.json", "evidence.json"}
            names = self._archive.namelist()
            for run_id, digest in self._index.recordings.items():
                if run_id not in {item.snapshot.run_id for item in self.evidence.runs}:
                    raise ValueError("exchange recording has no run evidence")
                prefix = _recording_prefix(run_id)
                recording = MeasurementSnapshot(self._archive, prefix=prefix)
                if (
                    recording.header.run_id != run_id
                    or recording.content_hash != digest
                ):
                    raise ValueError("exchange recording identity differs")
                self._recordings[run_id] = recording
                expected.update(name for name in names if name.startswith(prefix))
            for ref in self._index.payloads:
                name = _object_name(ref.digest)
                expected.add(name)
                if self._archive.getinfo(name).file_size != ref.size:
                    raise ValueError("exchange payload size differs")
            if len(names) != len(expected) or set(names) != expected:
                raise ValueError("exchange has missing, duplicate or unknown members")
        except Exception:
            self.close()
            raise

    @property
    def content_hash(self) -> str:
        return sha256_json_hash(self._index.model_dump(mode="json"))

    @property
    def payloads(self) -> tuple[PayloadReference, ...]:
        return self._index.payloads

    def recording(self, run_id: str) -> MeasurementSnapshot:
        """Return an existing recording; absence is not an empty dataset."""
        return self._recordings[run_id]

    def verify(self) -> None:
        for snapshot in self._recordings.values():
            snapshot.verify()
        for digest in {ref.digest for ref in self.payloads}:
            checksum = hashlib.sha256()
            with self._archive.open(_object_name(digest)) as source:
                while block := source.read(1024 * 1024):
                    checksum.update(block)
            actual = f"sha256:{checksum.hexdigest()}"
            if actual != digest:
                raise ValueError("exchange payload checksum differs")

    def close(self) -> None:
        self._archive.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
