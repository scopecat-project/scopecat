"""Explicitly retained direct receipts; saving never calls an instrument.

Small append-only catalog entries reuse durable events. Large immutable receipts
use the existing object store and current-format snapshot ownership.
"""

import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from scopecat.control.models import DurableEventInput
from scopecat.daemon.hardware_receipt_wire import encode_collect_receipt
from scopecat.records.instrument import InstrumentStateSnapshot
from scopecat.records.setup import SetupRevisionRef
from scopecat.sdk.instruments.commands import InteractiveCollectIntent
from scopecat.sdk.instruments.contracts import InstrumentDescription

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.instruments.runtime import InstrumentRuntime
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.data_cleanup import require_retained_references
from scopecat_server.storage.sqlite.data_reference_schema import (
    DIRECT_MEASUREMENT_EVENT_KIND,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

_KIND = DIRECT_MEASUREMENT_EVENT_KIND
_DISCARDED_KIND = "direct_measurement_discarded_v1"


class DirectMeasurementIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=200)
    instrument_id: str = Field(min_length=1, max_length=200)
    command_id: str = Field(min_length=1, max_length=200)


class DirectMeasurementDiscardReceipt(BaseModel):
    status: Literal["discarded", "saved", "not_retained"]
    record_id: str | None = None


class DirectMeasurementSave(DirectMeasurementIdentity):
    title: str = Field(default="", max_length=500)
    series_id: str = Field(default="", max_length=200)
    line_id: str = Field(default="", max_length=200)
    line_label: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=4000)


class PendingDirectMeasurement(BaseModel):
    selection: DirectMeasurementSave
    collected_at: datetime
    description: InstrumentDescription
    intent: InteractiveCollectIntent


class PendingDirectMeasurementPage(BaseModel):
    items: list[PendingDirectMeasurement]


class DirectMeasurementNoteCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=200)
    note: str = Field(max_length=4000)
    anomalous: bool = False


class DirectMeasurementNote(DirectMeasurementNoteCommand):
    recorded_at: datetime


class _NoteEvent(BaseModel):
    record_id: str
    annotation: DirectMeasurementNote


class DirectMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    saved_at: datetime
    collected_at: datetime
    selection: DirectMeasurementSave
    setup: SetupRevisionRef
    description: InstrumentDescription
    intent: InteractiveCollectIntent
    observed_before_collection: InstrumentStateSnapshot | None
    receipt_digest: str
    notes: list[DirectMeasurementNote] = Field(default_factory=list)


class DirectMeasurementPage(BaseModel):
    items: list[DirectMeasurement]
    next_cursor: int | None = None


def measurement_id(selection: DirectMeasurementIdentity) -> str:
    identity = [selection.session_id, selection.instrument_id, selection.command_id]
    import json

    return sha256(json.dumps(identity).encode()).hexdigest()


class DirectMeasurements:
    def __init__(
        self, store: SQLiteProjectStore, instruments: InstrumentRuntime
    ) -> None:
        self.store = store
        self.instruments = instruments
        self.control = SQLiteControlPlane(store.sqlite)

    def get(self, record_id: str) -> DirectMeasurement:
        with self.store.sqlite.read_connection() as connection:
            record = self._find(connection, record_id)
            if record is not None:
                record = record.model_copy(
                    update={"notes": self._notes(connection, record_id)}
                )
        if record is None:
            raise BackendNotFound("saved direct measurement not found")
        return record

    @staticmethod
    def _find(
        connection: sqlite3.Connection, record_id: str
    ) -> DirectMeasurement | None:
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                "SELECT payload_json FROM durable_events WHERE kind=? "
                "AND json_extract(payload_json, '$.id')=? LIMIT 1",
                (_KIND, record_id),
            ).fetchone(),
        )
        return (
            None
            if row is None
            else DirectMeasurement.model_validate_json(cast("str", row[0]))
        )

    def list(
        self, *, before: int | None = None, limit: int = 50
    ) -> DirectMeasurementPage:
        with self.store.sqlite.read_connection() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT event_id,payload_json FROM durable_events WHERE kind=? "
                    "AND (? IS NULL OR event_id<?) ORDER BY event_id DESC LIMIT ?",
                    (_KIND, before, before, limit + 1),
                ).fetchall(),
            )
        return DirectMeasurementPage(
            items=[
                DirectMeasurement.model_validate_json(cast("str", row[1]))
                for row in rows[:limit]
            ],
            next_cursor=cast("int", rows[limit - 1][0]) if len(rows) > limit else None,
        )

    def pending(self, instrument_id: str) -> PendingDirectMeasurementPage:
        with self.instruments.pending_collections.disposition_lock:
            return PendingDirectMeasurementPage(
                items=[
                    PendingDirectMeasurement(
                        selection=DirectMeasurementSave(
                            session_id=key[0], instrument_id=key[1], command_id=key[2]
                        ),
                        collected_at=value[2].collected_at,
                        description=value[1],
                        intent=value[2].intent,
                    )
                    for key, value in self.instruments.pending_collections.list(
                        instrument_id
                    )
                ]
            )

    def pending_receipt(self, selection: DirectMeasurementIdentity) -> bytes:
        with self.instruments.pending_collections.disposition_lock:
            _, _, replay = self.instruments.retained_collection(
                selection.session_id, selection.instrument_id, selection.command_id
            )
            return encode_collect_receipt(replay.receipt)

    def save(self, selection: DirectMeasurementSave) -> DirectMeasurement:
        # All disposition operations share this lock, including another service
        # facade for the same runtime. A timed-out HTTP caller does not cancel it.
        with self.instruments.pending_collections.disposition_lock:
            record = self._save(selection)
            self.instruments.pending_collections.release(
                (selection.session_id, selection.instrument_id, selection.command_id)
            )
            return record

    def _save(self, selection: DirectMeasurementSave) -> DirectMeasurement:
        record_id = measurement_id(selection)
        # Check retained identity before resolving a volatile session: retry works
        # even after close/restart and cannot collect a second time.
        with self.store.sqlite.read_connection() as connection:
            previous = self._find(connection, record_id)
            discarded = self._was_discarded(connection, record_id)
        if previous is not None:
            return self._same_selection(previous, selection)
        if discarded:
            raise BackendConflict("collection was explicitly discarded")
        session, description, replay = self.instruments.retained_collection(
            selection.session_id,
            selection.instrument_id,
            selection.command_id,
        )
        digest = self.store.objects.put(encode_collect_receipt(replay.receipt)).digest
        record = DirectMeasurement(
            id=record_id,
            saved_at=datetime.now(UTC),
            collected_at=replay.collected_at,
            selection=selection,
            setup=session.setup,
            description=description,
            intent=replay.intent,
            observed_before_collection=replay.observed_before_collection,
            receipt_digest=digest,
        )
        with self.store.sqlite.write_transaction() as connection:
            previous = self._find(connection, record_id)
            if previous is not None:
                return self._same_selection(previous, selection)
            # Cleanup uses this same writer. A save validated before deletion
            # cannot publish a new retained reference after the deletion fence.
            require_retained_references(connection, record.model_dump_json())
            self.control.append_event_in_transaction(
                connection,
                DurableEventInput(
                    kind=_KIND,
                    payload=record.model_dump(mode="json"),
                ),
            )
        return record

    @staticmethod
    def _was_discarded(connection: sqlite3.Connection, record_id: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM durable_events WHERE kind=? "
                "AND json_extract(payload_json, '$.id')=? LIMIT 1",
                (_DISCARDED_KIND, record_id),
            ).fetchone()
            is not None
        )

    def discard(
        self, selection: DirectMeasurementIdentity
    ) -> DirectMeasurementDiscardReceipt:
        key = (selection.session_id, selection.instrument_id, selection.command_id)
        record_id = measurement_id(selection)
        with self.instruments.pending_collections.disposition_lock:
            with self.store.sqlite.write_transaction() as connection:
                saved = self._find(connection, record_id)
                if saved is not None:
                    receipt = DirectMeasurementDiscardReceipt(
                        status="saved", record_id=record_id
                    )
                else:
                    if not self._was_discarded(connection, record_id):
                        if self.instruments.pending_collections.find(key) is None:
                            return DirectMeasurementDiscardReceipt(
                                status="not_retained"
                            )
                        # Only the identity is durable. Unsaved payloads are not
                        # silently persisted; this tombstone makes retry safe.
                        self.control.append_event_in_transaction(
                            connection,
                            DurableEventInput(
                                kind=_DISCARDED_KIND,
                                payload={"id": record_id},
                            ),
                        )
                    receipt = DirectMeasurementDiscardReceipt(status="discarded")
            # Release only after commit. On any storage error, retain the result
            # so the same identity can be retried after an unknown response.
            self.instruments.pending_collections.release(key)
            return receipt

    @staticmethod
    def _same_selection(
        record: DirectMeasurement, selection: DirectMeasurementSave
    ) -> DirectMeasurement:
        if record.selection != selection:
            raise BackendConflict(
                "saved measurement attribution is immutable; collect a new attempt"
            )
        return record

    @staticmethod
    def _notes(
        connection: sqlite3.Connection, record_id: str
    ) -> list[DirectMeasurementNote]:
        rows = cast(
            "list[sqlite3.Row]",
            connection.execute(
                "SELECT payload_json FROM durable_events WHERE kind=? "
                "AND json_extract(payload_json, '$.record_id')=? ORDER BY event_id",
                ("direct_measurement_note_v1", record_id),
            ).fetchall(),
        )
        return [
            _NoteEvent.model_validate_json(cast("str", row[0])).annotation
            for row in rows
        ]

    def annotate(
        self, record_id: str, command: DirectMeasurementNoteCommand
    ) -> DirectMeasurement:
        self.get(record_id)
        with self.store.sqlite.write_transaction() as connection:
            notes = self._notes(connection, record_id)
            previous = next(
                (note for note in notes if note.operation_id == command.operation_id),
                None,
            )
            if previous is not None:
                if previous.model_dump(exclude={"recorded_at"}) != command.model_dump():
                    raise BackendConflict(
                        "annotation operation id already has different content"
                    )
            else:
                annotation = DirectMeasurementNote(
                    operation_id=command.operation_id,
                    note=command.note,
                    anomalous=command.anomalous,
                    recorded_at=datetime.now(UTC),
                )
                self.control.append_event_in_transaction(
                    connection,
                    DurableEventInput(
                        kind="direct_measurement_note_v1",
                        payload={
                            "record_id": record_id,
                            "annotation": annotation.model_dump(mode="json"),
                        },
                    ),
                )
        return self.get(record_id)

    def receipt(self, record_id: str) -> bytes:
        return self.store.objects.read(self.get(record_id).receipt_digest)
