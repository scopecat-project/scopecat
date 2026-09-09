"""Validate server-recorded preview footprints against the existing event order."""

import json
import sqlite3
from datetime import datetime
from typing import cast

from scopecat.control.models import DurableEventInput
from scopecat.records.manual_preview import (
    ManualPreviewBinding,
    ManualPreviewChange,
    ManualPreviewFence,
    ManualPreviewRecord,
    ManualPreviewValidity,
    PreviewInstrument,
)

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane


class ManualPreviewChanged(ValueError):
    """Manual operations changed relevant preview facts; obtain a fresh preview."""


class ManualPreviewRepository:
    def __init__(self, sqlite: SQLiteDatabase) -> None:
        self.sqlite = sqlite

    def cursor(self) -> int:
        # Capture before invoking project preview code or reading instrument facts.
        with self.sqlite.read_connection() as connection:
            return cast(
                "int",
                connection.execute(
                    "SELECT COALESCE(MAX(event_id), 0) FROM durable_events"
                ).fetchone()[0],
            )

    def record(
        self,
        *,
        cursor: int,
        binding: ManualPreviewBinding,
        instruments: tuple[PreviewInstrument, ...],
    ) -> ManualPreviewFence:
        record = ManualPreviewRecord(
            observed_cursor=cursor,
            binding=binding,
            instruments=instruments,
        )
        with self.sqlite.write_transaction() as connection:
            event = SQLiteControlPlane(self.sqlite).append_event_in_transaction(
                connection,
                DurableEventInput(
                    kind="launch_preview_checked",
                    payload=record.model_dump(mode="json"),
                ),
            )
        return ManualPreviewFence(event_id=event.event_id, binding=binding)

    def validity(self, fence: ManualPreviewFence) -> ManualPreviewValidity:
        with self.sqlite.read_connection() as connection:
            return self.validity_in_transaction(connection, fence)

    @staticmethod
    def validity_in_transaction(
        connection: sqlite3.Connection,
        fence: ManualPreviewFence,
    ) -> ManualPreviewValidity:
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                "SELECT kind, payload_json FROM durable_events WHERE event_id = ?",
                (fence.event_id,),
            ).fetchone(),
        )
        if row is None or row["kind"] != "launch_preview_checked":
            raise ManualPreviewChanged("Checked preview was not found; preview again")
        record = ManualPreviewRecord.model_validate_json(
            cast("str", row["payload_json"])
        )
        if record.binding != fence.binding:
            raise ManualPreviewChanged("Preview binding changed; preview again")
        keys = {item.exclusivity_key for item in record.instruments}
        if not keys:
            return ManualPreviewValidity(valid=True)
        changes: list[ManualPreviewChange] = []
        rows = connection.execute(
            "SELECT kind, payload_json, occurred_at FROM durable_events "
            "WHERE event_id > ? AND kind IN ("
            "'instrument_apply_started', 'instrument_apply_finished', "
            "'instrument_invoke_started', 'instrument_invoke_finished', "
            "'instrument_collect_started', 'instrument_collect_finished', "
            "'instrument_connection_release_started', "
            "'instrument_session_abort_started') ORDER BY event_id",
            (record.observed_cursor,),
        )
        seen: set[tuple[str, str]] = set()
        for event in cast("list[sqlite3.Row]", rows.fetchall()):
            payload = cast(
                "dict[str, object]", json.loads(cast("str", event["payload_json"]))
            )
            kind = cast("str", event["kind"])
            if kind in {
                "instrument_connection_release_started",
                "instrument_session_abort_started",
            }:
                touched = set(cast("list[str]", payload["exclusivity_keys"]))
                action = (
                    "abort" if kind == "instrument_session_abort_started" else "release"
                )
                operation = str(payload["operation_id"])
            else:
                session = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        "SELECT instrument_ids_json, exclusivity_keys_json "
                        "FROM instrument_sessions WHERE session_id = ?",
                        (payload["session_id"],),
                    ).fetchone(),
                )
                assert session is not None
                ids = cast(
                    "list[str]", json.loads(cast("str", session["instrument_ids_json"]))
                )
                physical = cast(
                    "list[str]",
                    json.loads(cast("str", session["exclusivity_keys_json"])),
                )
                mapping = dict(zip(ids, physical, strict=True))
                touched = {mapping[cast("str", payload["instrument_id"])]}
                action = kind.split("_")[1]
                operation = str(payload["operation_id"])
            affected = keys & touched
            if not affected or (action, operation) in seen:
                continue
            seen.add((action, operation))
            ids = tuple(
                item.instrument_id
                for item in record.instruments
                if item.exclusivity_key in affected
            )
            changes.append(
                ManualPreviewChange(
                    instrument_ids=ids,
                    action=action,
                    occurred_at=datetime.fromisoformat(
                        cast("str", event["occurred_at"])
                    ),
                    reason=(
                        "Connection was released; "
                        "connection continuity must be checked again."
                        if action == "release"
                        else (
                            f"Manual {action} may change instrument state, "
                            "including a partial or failed operation."
                        )
                    ),
                )
            )
        return ManualPreviewValidity(valid=not changes, changes=tuple(changes))

    @staticmethod
    def require_valid_in_transaction(
        connection: sqlite3.Connection,
        fence: ManualPreviewFence,
    ) -> None:
        validity = ManualPreviewRepository.validity_in_transaction(connection, fence)
        if not validity.valid:
            ids = sorted(
                {item for change in validity.changes for item in change.instrument_ids}
            )
            raise ManualPreviewChanged(
                f"Manual operation changed {', '.join(ids)} since preview; "
                "preview again"
            )
