"""Verified evidence imports into the application's existing store."""

import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from scopecat.data_exchange import ScientificExchange
from scopecat.kernel.content_identity import sha256_json_hash

from .project_store import SQLiteProjectStore


class CaptureConflict(ValueError):
    """A source-qualified run already has a different retained capture."""


@dataclass(frozen=True)
class ImportedCapture:
    path: Path
    content_hash: str
    created: bool


def import_scientific_capture(
    store: SQLiteProjectStore, source: Path
) -> ImportedCapture:
    """Retain a whole capture; do not register runnable history or local devices.

    Conflicts apply to source-qualified runs, including runs shared by differently
    selected export packages. Repacking identical content is idempotent.
    """
    with tempfile.TemporaryDirectory(prefix="scopecat-import-") as temporary:
        staged = Path(temporary) / "capture.scopecat"
        shutil.copyfile(source, staged)
        with ScientificExchange(staged) as capture:
            capture.verify()
            project = capture.evidence.source_project_id
            capture_hash = capture.content_hash
            identities: dict[str, str] = {}
            for run in capture.evidence.runs:
                run_id = run.snapshot.run_id
                try:
                    recording_hash = capture.recording(run_id).content_hash
                except KeyError:
                    recording_hash = None
                identities[run_id] = sha256_json_hash(
                    {
                        "run": run.model_dump(mode="json"),
                        "recording": recording_hash,
                        "payloads": [
                            ref.model_dump(mode="json")
                            for ref in capture.payloads
                            if ref.owner_kind == "run" and ref.owner_id == run_id
                        ],
                    }
                )
            roots = json.dumps(capture.evidence.roots)
        with store.sqlite.write_transaction() as connection:
            for run_id, content_hash in identities.items():
                previous = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        "SELECT content_hash FROM imported_run_identities "
                        "WHERE source_project_id=? AND run_id=?",
                        (project, run_id),
                    ).fetchone(),
                )
                if previous is not None and previous[0] != content_hash:
                    raise CaptureConflict(
                        f"imported run has different content: {project}/{run_id}; "
                        "the earlier capture was retained"
                    )
            existing = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT object_digest FROM imported_captures WHERE content_hash=?",
                    (capture_hash,),
                ).fetchone(),
            )
            if existing is not None:
                digest = cast("str", existing[0])
                store.objects.verify(digest)
                return ImportedCapture(
                    store.objects.path_for(digest), capture_hash, False
                )
            retained = store.objects.put_file(staged)
            connection.execute(
                "INSERT INTO imported_captures VALUES (?, ?, ?, ?)",
                (capture_hash, project, retained.digest, roots),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO imported_run_identities VALUES (?, ?, ?, ?)",
                [
                    (project, run_id, digest, capture_hash)
                    for run_id, digest in identities.items()
                ],
            )
        return ImportedCapture(
            store.objects.path_for(retained.digest), capture_hash, True
        )
