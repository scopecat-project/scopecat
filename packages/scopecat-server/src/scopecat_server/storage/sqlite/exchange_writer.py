"""Assemble captured evidence and stored bytes inside one caller-owned snapshot."""

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import cast

from scopecat.data_exchange import (
    PayloadReference,
    PayloadSource,
    write_scientific_exchange,
)
from scopecat.data_exchange.models import ScientificEvidence
from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash
from scopecat.runs.refs import content_entry_ref

from scopecat_server.storage.sqlite.evidence_analysis import capture_analysis_evidence
from scopecat_server.storage.sqlite.evidence_inputs import capture_sample_payloads
from scopecat_server.storage.sqlite.measurement_export import (
    export_measurement_snapshot_in_transaction,
)
from scopecat_server.storage.sqlite.object_store import ImmutableObjectStore
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.resource_objects import resource_directory
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


def write_captured_exchange(
    connection: sqlite3.Connection,
    store: SQLiteProjectStore,
    evidence: ScientificEvidence,
    destination: Path,
) -> None:
    """Compose a resolved capture; this function does not select its closure.

    Keep the transaction used to capture ``evidence`` open through this call.
    Staging is private to this operation; no partial destination is published.
    """
    runs = SQLiteRunRepository(store.sqlite, store.objects.root)
    payloads: dict[tuple[str, str, str], PayloadSource] = {}

    def include(payload: PayloadSource) -> None:
        ref = payload.reference
        identity = (ref.owner_kind, ref.owner_id, ref.ref)
        previous = payloads.get(identity)
        if previous is not None and previous.reference != ref:
            raise ValueError("captured payload has conflicting identities")
        payloads[identity] = payload

    for run in evidence.runs:
        run_id = run.snapshot.run_id
        objects = ImmutableObjectStore(resource_directory(runs.objects, "run", run_id))
        for entry in run.contents:
            if entry.role == "dataset" and entry.kind == "measurement_dataset":
                # The recording partition owns this logical dataset. Its identity
                # is checked from selected records by ScientificExchange.verify.
                continue
            ref = content_entry_ref(entry)
            row = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT digest FROM run_repository_refs WHERE run_id=? AND ref=?",
                    (run_id, ref),
                ).fetchone(),
            )
            if row is None:
                raise KeyError(f"missing run content: {run_id}/{ref}")
            digest = cast("str", row[0])
            actual = digest
            if entry.role == "record":
                actual = stable_content_hash(
                    content_fingerprint(
                        cast("object", json.loads(objects.read(digest)))
                    )
                )
            if actual != entry.content_hash:
                raise ValueError(
                    f"run content differs from its retained identity: {run_id}/{ref}"
                )
            path = objects.path_for(digest)
            include(
                PayloadSource(
                    PayloadReference(
                        owner_kind="run",
                        owner_id=run_id,
                        ref=ref,
                        digest=digest,
                        size=path.stat().st_size,
                    ),
                    path,
                )
            )
    for analysis in evidence.analyses:
        captured = capture_analysis_evidence(
            connection, runs, analysis.record.subject, analysis.entry.id
        )
        if captured.evidence != analysis:
            raise ValueError("analysis changed across the evidence capture boundary")
        subject = analysis.record.subject
        for payload in captured.payloads:
            include(
                PayloadSource(
                    PayloadReference(
                        owner_kind="run" if subject.kind == "run" else "analysis",
                        owner_id=subject.run_id
                        if subject.kind == "run"
                        else analysis.entry.id,
                        ref=payload.ref,
                        digest=payload.digest,
                        size=payload.path.stat().st_size,
                    ),
                    payload.path,
                )
            )
    for payload in capture_sample_payloads(store, evidence.inputs.samples):
        include(payload)
    with tempfile.TemporaryDirectory(
        prefix=".capture-", dir=destination.parent
    ) as temporary:
        recordings: dict[str, Path] = {}
        for run in evidence.runs:
            run_id = run.snapshot.run_id
            if (
                connection.execute(
                    "SELECT 1 FROM execution_measurement_headers WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                is None
            ):
                continue
            path = Path(temporary) / hashlib.sha256(run_id.encode()).hexdigest()
            export_measurement_snapshot_in_transaction(connection, runs, run_id, path)
            recordings[run_id] = path
        write_scientific_exchange(destination, evidence, recordings, payloads.values())
