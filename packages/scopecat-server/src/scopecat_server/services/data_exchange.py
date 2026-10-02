"""Application data exchange without restoring execution authority."""

import json
import sqlite3
from pathlib import Path
from typing import Literal, cast

from scopecat.data_exchange import ScientificExchange
from scopecat.data_exchange.models import (
    CaptureImportReceipt,
    CaptureRecordingPage,
    CaptureSummary,
    ScientificEvidence,
)

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.storage.sqlite.exchange_import import (
    CaptureConflict,
    import_scientific_capture,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


class DataExchangeService:
    def __init__(self, store: SQLiteProjectStore):
        self._store = store

    def import_file(self, source: Path) -> CaptureImportReceipt:
        try:
            imported = import_scientific_capture(self._store, source)
        except CaptureConflict as error:
            raise BackendConflict(str(error)) from error
        return CaptureImportReceipt(
            capture=self.get(imported.content_hash), created=imported.created
        )

    @staticmethod
    def _summary(row: sqlite3.Row) -> CaptureSummary:
        return CaptureSummary.model_validate(
            {
                "content_hash": row["content_hash"],
                "source_project_id": row["source_project_id"],
                "roots": json.loads(cast("str", row["roots_json"])),
            }
        )

    def list(self, *, offset: int = 0, limit: int = 100) -> tuple[CaptureSummary, ...]:
        with self._store.sqlite.read_connection() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT * FROM imported_captures "
                    "ORDER BY content_hash LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall(),
            )
        return tuple(self._summary(row) for row in rows)

    def _row(self, content_hash: str) -> sqlite3.Row:
        with self._store.sqlite.read_connection() as connection:
            row = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT * FROM imported_captures WHERE content_hash=?",
                    (content_hash,),
                ).fetchone(),
            )
        if row is None:
            raise BackendNotFound("imported capture does not exist")
        return row

    def get(self, content_hash: str) -> CaptureSummary:
        return self._summary(self._row(content_hash))

    def path(self, content_hash: str) -> Path:
        digest = cast("str", self._row(content_hash)["object_digest"])
        return self._store.objects.path_for(digest)

    def evidence(self, content_hash: str) -> ScientificEvidence:
        with ScientificExchange(self.path(content_hash)) as capture:
            if capture.content_hash != content_hash:
                raise ValueError("stored capture identity differs")
            return capture.evidence

    def download(self, content_hash: str) -> Path:
        row = self._row(content_hash)
        self._store.objects.verify(cast("str", row["object_digest"]))
        return self._store.objects.path_for(cast("str", row["object_digest"]))

    def recording_page(
        self,
        content_hash: str,
        run_id: str,
        *,
        selection: Literal["acquired", "selected"],
        offset: int,
        limit: int,
    ) -> CaptureRecordingPage:
        with ScientificExchange(self.path(content_hash)) as capture:
            try:
                recording = capture.recording(run_id)
            except KeyError as error:
                raise BackendNotFound(
                    "this capture has no recording for the run"
                ) from error
            if selection == "selected":
                count = recording.selected_record_count
                if count is None:
                    raise BackendConflict(
                        "this recording has no retained analysis selection"
                    )
                items = tuple(recording.selected_records(offset=offset, limit=limit))
            else:
                count = recording.record_count
                items = tuple(recording.records(offset=offset, limit=limit))
            following = offset + len(items)
            return CaptureRecordingPage(
                dataset_schema=recording.header.dataset_schema,
                selection=selection,
                record_count=count,
                selected_record_count=recording.selected_record_count,
                offset=offset,
                next_offset=following if following < count else None,
                items=items,
            )
