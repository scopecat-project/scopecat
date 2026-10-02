"""Read scientific evidence and recording partitions without an application."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import chain
from pathlib import Path
from typing import Literal, Self, cast
from zipfile import ZIP_STORED, ZipFile

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.analysis.repository import AnalysisPublication
from scopecat.data_exchange.models import AnalysisEvidence, ScientificEvidence
from scopecat.kernel.content_identity import (
    content_fingerprint,
    sha256_content_hash,
    sha256_json_hash,
    stable_content_hash,
)
from scopecat.measurements.archive import MeasurementSnapshot
from scopecat.records.analysis import AnalysisRecord
from scopecat.records.content import ContentEntry, Sha256ContentHash
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.records.sample_artifact import is_owned_sample_artifact_uri
from scopecat.runs.refs import content_entry_ref

MAX_INDEX_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024


class PayloadReference(BaseModel):
    """An inert logical reference to bytes; no archive path comes from the ref."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_kind: Literal["run", "analysis", "sample"]
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
    _write_scientific_exchange(destination, evidence, recordings, payloads)


def _write_scientific_exchange(
    destination: Path,
    evidence: ScientificEvidence,
    recordings: Mapping[str, Path],
    payloads: Iterable[PayloadSource],
    *,
    source: tuple[ZipFile, _Index] | None = None,
) -> None:
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
            if source is not None:
                source_archive, source_index = source
                recording_hashes.update(source_index.recordings)
                references.extend(source_index.payloads)
                copied.update(ref.digest for ref in source_index.payloads)
                for name in source_archive.namelist():
                    if name in {"index.json", "evidence.json"}:
                        continue
                    with (
                        source_archive.open(name) as src,
                        output.open(name, "w", force_zip64=True) as dst,
                    ):
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
            for run_id, path in sorted(recordings.items()):
                with ZipFile(path) as recording_archive:
                    with MeasurementSnapshot(recording_archive) as snapshot:
                        snapshot.verify()
                        if snapshot.header.run_id != run_id:
                            raise ValueError("exchange recording identity differs")
                        recording_hashes[run_id] = snapshot.content_hash
                    for name in recording_archive.namelist():
                        with (
                            recording_archive.open(name) as src,
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

    def write_analyses(
        self, destination: Path, publications: Iterable[AnalysisPublication]
    ) -> None:
        """Append independent analyses to a new file, preserving source runs.

        Publications use the same prepared records and payloads as application
        analysis storage. Run-owned publications must be saved at their original
        authority; external analysis instead retains those runs as inputs.
        The original file and any existing destination are never overwritten.
        """
        analyses = list(self.evidence.analyses)
        payloads: list[PayloadSource] = []
        with tempfile.TemporaryDirectory(prefix="scopecat-analysis-") as directory:
            for publication in publications:
                if publication.subject.kind == "run":
                    raise ValueError(
                        "external analysis must not change source run ownership"
                    )
                record_ref = content_entry_ref(publication.record)
                record = next(
                    item.value for item in publication.models if item.ref == record_ref
                )
                if not isinstance(record, AnalysisRecord):
                    raise TypeError("analysis publication requires an AnalysisRecord")
                analyses.append(
                    AnalysisEvidence(
                        entry=publication.record,
                        record=record,
                        published_at=datetime.now(UTC),
                        contents=publication.entries,
                    )
                )
                contents = chain(
                    (
                        (item.ref, item.value.model_dump_json().encode())
                        for item in publication.models
                    ),
                    ((item.ref, item.content) for item in publication.bytes),
                )
                for ref, content in contents:
                    path = Path(directory) / str(len(payloads))
                    path.write_bytes(content)
                    payloads.append(
                        PayloadSource(
                            PayloadReference(
                                owner_kind="analysis",
                                owner_id=publication.record.id,
                                ref=ref,
                                digest=sha256_content_hash(content),
                                size=len(content),
                            ),
                            path,
                        )
                    )
            _write_scientific_exchange(
                destination,
                self.evidence.model_copy(update={"analyses": tuple(analyses)}),
                {},
                payloads,
                source=(self._archive, self._index),
            )

    def copy_payload(self, reference: PayloadReference, destination: Path) -> None:
        """Save one retained attachment after checking its owned bytes.

        The caller chooses the destination. Historical filenames and logical refs
        never become extraction paths, and existing user files are not replaced.
        """
        if reference not in self.payloads:
            raise KeyError("payload reference does not belong to this exchange")
        with tempfile.NamedTemporaryFile(
            prefix=".exchange-payload-", dir=destination.parent, delete=False
        ) as temp:
            staged = Path(temp.name)
        try:
            checksum = hashlib.sha256()
            with (
                self._archive.open(_object_name(reference.digest)) as source,
                staged.open("wb") as output,
            ):
                while block := source.read(1024 * 1024):
                    output.write(block)
                    checksum.update(block)
            if f"sha256:{checksum.hexdigest()}" != reference.digest:
                raise ValueError("exchange payload checksum differs")
            os.link(staged, destination)
        finally:
            staged.unlink(missing_ok=True)

    def _verify_content_index(self) -> None:
        references = {
            (ref.owner_kind, ref.owner_id, ref.ref): ref for ref in self.payloads
        }

        def verify_entry(owner_kind: str, owner_id: str, entry: ContentEntry) -> None:
            if entry.role == "dataset" and entry.kind == "measurement_dataset":
                return  # Verified against its recording partition below.
            identity = (owner_kind, owner_id, content_entry_ref(entry))
            ref = references.get(identity)
            if ref is None:
                raise ValueError(f"exchange is missing retained content: {identity}")
            actual = ref.digest
            if entry.role == "record":
                if ref.size > MAX_EVIDENCE_BYTES:
                    raise ValueError("exchange record exceeds the metadata budget")
                actual = stable_content_hash(
                    content_fingerprint(
                        cast(
                            "object",
                            json.loads(self._archive.read(_object_name(ref.digest))),
                        )
                    )
                )
            if actual != entry.content_hash:
                raise ValueError(
                    f"exchange retained content identity differs: {identity}"
                )

        for run in self.evidence.runs:
            for entry in run.contents:
                verify_entry("run", run.snapshot.run_id, entry)
        for analysis in self.evidence.analyses:
            subject = analysis.record.subject
            for entry in analysis.contents:
                verify_entry(
                    "run" if subject.kind == "run" else "analysis",
                    subject.run_id if subject.kind == "run" else analysis.entry.id,
                    entry,
                )
        for revision in self.evidence.inputs.samples:
            for artifact in revision.content.artifacts:
                if not is_owned_sample_artifact_uri(artifact.uri):
                    continue
                reference = references.get(
                    (
                        "sample",
                        revision.sample_id,
                        f"revisions/{revision.revision}/artifacts/{artifact.id}",
                    )
                )
                if reference is None or reference.digest != artifact.uri:
                    raise ValueError("exchange sample attachment is missing or differs")

    def _proposals(self) -> tuple[ParameterChangeProposal, ...]:
        references = {
            (ref.owner_kind, ref.owner_id, ref.ref): ref for ref in self.payloads
        }
        entries = [
            ("run", run.snapshot.run_id, entry)
            for run in self.evidence.runs
            for entry in run.contents
        ]
        for analysis in self.evidence.analyses:
            subject = analysis.record.subject
            entries.extend(
                (
                    "run" if subject.kind == "run" else "analysis",
                    subject.run_id if subject.kind == "run" else analysis.entry.id,
                    entry,
                )
                for entry in analysis.contents
            )
        proposals: dict[tuple[str, str, str], ParameterChangeProposal] = {}
        for owner_kind, owner_id, entry in entries:
            if entry.role != "record" or entry.kind != "parameter_change_proposal":
                continue
            identity = (owner_kind, owner_id, content_entry_ref(entry))
            if identity in proposals:
                continue
            ref = references[identity]  # Content-index verification runs first.
            proposal = ParameterChangeProposal.model_validate_json(
                self._archive.read(_object_name(ref.digest))
            )
            if proposal.id != entry.id or (
                owner_kind == "run" and proposal.source_run_id != owner_id
            ):
                raise ValueError("proposal record differs from its retained owner")
            proposals[identity] = proposal
        return tuple(proposals.values())

    def verify(self) -> None:
        from .input_references import validate_input_references
        from .proposals import validate_proposal_references
        from .references import validate_analysis_references

        validate_analysis_references(self.evidence)
        self._verify_content_index()
        proposals = self._proposals()
        validate_input_references(self.evidence.inputs, (self.evidence, *proposals))
        validate_proposal_references(self.evidence, proposals)
        for snapshot in self._recordings.values():
            snapshot.verify()
        for run in self.evidence.runs:
            for entry in run.contents:
                if entry.role != "dataset" or entry.kind != "measurement_dataset":
                    continue
                snapshot = self._recordings.get(run.snapshot.run_id)
                if snapshot is None:
                    raise ValueError(
                        "retained measurement dataset has no recording partition"
                    )
                if (
                    snapshot.header.dataset_schema.dataset_id != entry.id
                    or snapshot.header.dataset_schema.model_dump(mode="json")
                    != entry.data_schema
                    or snapshot.selected_content_hash != entry.content_hash
                ):
                    raise ValueError(
                        "recording differs from its retained measurement dataset"
                    )
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
