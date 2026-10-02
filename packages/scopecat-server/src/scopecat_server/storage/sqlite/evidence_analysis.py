"""Capture analysis publication metadata and immutable output locations."""

import json
import sqlite3
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from scopecat.data_exchange.models import AnalysisEvidence
from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash
from scopecat.records.analysis import (
    AnalysisArtifactRecordOutput,
    AnalysisDatasetRecordOutput,
    AnalysisFigureRecordOutput,
    AnalysisParameterProposalRecordOutput,
    AnalysisPublishedDatasetViewSource,
    AnalysisPublishedOutputReference,
    AnalysisRecord,
    AnalysisSubject,
    PublishedAnalysisRecordInput,
    RunAnalysisSubject,
    published_output_input_identity,
)
from scopecat.records.content import ContentEntry
from scopecat.runs.refs import record_content_ref

from scopecat_server.storage.sqlite.analysis_index import read_publication
from scopecat_server.storage.sqlite.object_store import ImmutableObjectStore
from scopecat_server.storage.sqlite.resource_objects import resource_directory
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


@dataclass(frozen=True)
class RetainedPayload:
    """Logical ref and exact bytes to stream into an exchange, not an import path."""

    ref: str
    digest: str
    path: Path


@dataclass(frozen=True)
class CapturedAnalysis:
    evidence: AnalysisEvidence
    payloads: tuple[RetainedPayload, ...]


def _published_dependencies(
    record: AnalysisRecord,
) -> Iterator[tuple[AnalysisPublishedOutputReference, tuple[str, str, str, str]]]:
    for item in record.inputs:
        if isinstance(item, PublishedAnalysisRecordInput):
            yield item.source, (item.kind, item.target, item.content_hash, item.codec)
    for output in record.outputs:
        if isinstance(output, AnalysisFigureRecordOutput):
            for layer in output.content.layers:
                source = layer.source
                if isinstance(source, AnalysisPublishedDatasetViewSource):
                    yield (
                        source.source,
                        (
                            "analysis_dataset",
                            source.dataset.dataset_id,
                            source.dataset.content_hash,
                            source.dataset.codec,
                        ),
                    )


def capture_analysis_input_graph(
    connection: sqlite3.Connection,
    runs: SQLiteRunRepository,
    roots: Iterable[tuple[AnalysisSubject, str]],
) -> tuple[CapturedAnalysis, ...]:
    """Capture published inputs and figure sources, checking exact output identity.

    All lookups use the caller's snapshot. Run, interpretation and revision
    dependencies are resolved by the enclosing scientific capture layer.
    """
    pending = deque(roots)
    captured: dict[tuple[AnalysisSubject, str], CapturedAnalysis] = {}
    while pending:
        identity = pending.popleft()
        if identity in captured:
            continue
        subject, record_id = identity
        publication = capture_analysis_evidence(connection, runs, subject, record_id)
        captured[identity] = publication
        for source, _ in _published_dependencies(publication.evidence.record):
            pending.append((source.subject, source.analysis_record_id))
    for publication in captured.values():
        for reference, expected in _published_dependencies(publication.evidence.record):
            source = captured[(reference.subject, reference.analysis_record_id)]
            output = next(
                (
                    output
                    for output in source.evidence.record.outputs
                    if output.id == reference.output_id
                ),
                None,
            )
            if published_output_input_identity(output) != expected:
                raise ValueError(
                    "analysis input evidence differs from its exact output"
                )
    return tuple(captured.values())


def capture_analysis_evidence(
    connection: sqlite3.Connection,
    runs: SQLiteRunRepository,
    subject: AnalysisSubject,
    record_id: str,
) -> CapturedAnalysis:
    """Capture one publication in the same transaction as its input evidence.

    Payload paths remain local to the exporter. A writer must check their digests
    while copying: cleanup may remove a file after this metadata capture. Never
    turn a missing file into an absent output or an empty placeholder.
    """
    publication = read_publication(connection, subject=subject, record_id=record_id)
    if publication is None:
        raise KeyError(f"missing analysis evidence: {record_id}")
    if isinstance(subject, RunAnalysisSubject):
        objects = ImmutableObjectStore(
            resource_directory(runs.objects, "run", subject.run_id)
        )
        rows = cast(
            "Iterator[sqlite3.Row]",
            connection.execute(
                "SELECT entry_json FROM run_contents WHERE run_id=? "
                "AND (produced_by=? OR (role='record' AND content_id=?)) "
                "ORDER BY role, content_id",
                (subject.run_id, record_id, record_id),
            ),
        )
        reference_query = (
            "SELECT ref,digest FROM run_repository_refs WHERE run_id=? AND ref=?"
        )
        reference_owner = subject.run_id
    else:
        objects = ImmutableObjectStore(
            resource_directory(runs.objects, "analysis", record_id)
        )
        rows = cast(
            "Iterator[sqlite3.Row]",
            connection.execute(
                "SELECT c.entry_json FROM project_analysis_contents c "
                "JOIN analysis_publications a ON a.sequence=c.publication_sequence "
                "WHERE a.record_id=? AND a.subject_kind!='run' ORDER BY c.content_id",
                (record_id,),
            ),
        )
        reference_query = (
            "SELECT r.ref,r.digest FROM project_analysis_repository_refs r "
            "JOIN analysis_publications a ON a.sequence=r.publication_sequence "
            "WHERE a.record_id=? AND a.subject_kind!='run' AND r.ref=?"
        )
        reference_owner = record_id
    contents = tuple(
        ContentEntry.model_validate_json(cast("str", row[0])) for row in rows
    )
    if isinstance(subject, RunAnalysisSubject):
        record = AnalysisRecord.model_validate_json(
            runs.read_bytes_in_transaction(
                connection,
                subject.run_id,
                record_content_ref(record_id=record_id, kind="analysis"),
            )
        )
        indexed = {(entry.role, entry.id): entry for entry in contents}
        for output in record.outputs:
            match output:
                case AnalysisParameterProposalRecordOutput():
                    role, content_id = "record", output.content.proposal_id
                case AnalysisArtifactRecordOutput():
                    role, content_id = "artifact", output.content.artifact_id
                case AnalysisDatasetRecordOutput():
                    role, content_id = "dataset", output.content.dataset_id
                case _:
                    continue
            if (role, content_id) not in indexed:
                indexed[(role, content_id)] = runs.read_content_in_transaction(
                    connection,
                    subject.run_id,
                    role=role,
                    content_id=content_id,
                )
        contents = tuple(
            sorted(indexed.values(), key=lambda entry: (entry.role, entry.id))
        )
    # Use exact canonical refs from the content index, not prefix/substring guesses
    # over run-owned objects that may belong to other publications.
    from scopecat.runs.refs import artifact_content_ref, dataset_content_ref

    entries_by_ref: dict[str, ContentEntry] = {}
    for entry in contents:
        match entry.role:
            case "record":
                ref = record_content_ref(record_id=entry.id, kind=entry.kind)
            case "artifact":
                ref = artifact_content_ref(artifact_id=entry.id, kind=entry.kind)
            case "dataset":
                ref = dataset_content_ref(dataset_id=entry.id, kind=entry.kind)
        entries_by_ref[ref] = entry
    record_ref = record_content_ref(record_id=record_id, kind="analysis")
    expected = set(entries_by_ref) | {record_ref}
    payloads: list[RetainedPayload] = []
    for ref in sorted(expected):
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                reference_query,
                (reference_owner, ref),
            ).fetchone(),
        )
        if row is None:
            raise ValueError("analysis evidence is missing a retained output reference")
        payloads.append(
            RetainedPayload(
                ref=ref,
                digest=cast("str", row["digest"]),
                path=objects.path_for(cast("str", row["digest"])),
            )
        )
    for payload in payloads:
        entry = entries_by_ref.get(payload.ref)
        if entry is None:
            continue  # The missing publication entry is rejected by AnalysisEvidence.
        actual = payload.digest
        if entry.role == "record":
            actual = stable_content_hash(
                content_fingerprint(
                    cast("object", json.loads(objects.read(payload.digest)))
                )
            )
        if actual != entry.content_hash:
            raise ValueError("analysis output bytes differ from their content identity")
    record_payload = next(item for item in payloads if item.ref == record_ref)
    record = AnalysisRecord.model_validate_json(objects.read(record_payload.digest))
    if (
        record.subject != subject
        or record.publication_hash != publication.publication_hash
        or record.revision != publication.revision
    ):
        raise ValueError("analysis evidence differs from its publication index")
    return CapturedAnalysis(
        AnalysisEvidence(
            entry=publication.record,
            record=record,
            published_at=publication.published_at,
            contents=contents,
        ),
        tuple(sorted(payloads, key=lambda item: item.ref)),
    )
