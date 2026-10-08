"""Append-only experiment edits and pre-submit intent, without execution authority."""

import sqlite3
from datetime import UTC, datetime
from typing import cast

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.launch_recovery import (
    LaunchAttemptPage,
    LaunchAttemptRecord,
    LaunchAttemptSave,
    LaunchDraftPage,
    LaunchDraftRecord,
    LaunchDraftSave,
    LaunchDraftTarget,
    LaunchDraftView,
)

LAUNCH_RECOVERY_TABLES_SQL = """
CREATE TABLE launch_drafts (
    revision INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL,
    experiment TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('saved','conflict','discarded')),
    command_json TEXT NOT NULL,
    draft_json TEXT NOT NULL
);
CREATE INDEX launch_drafts_target
ON launch_drafts(workspace_id,experiment,revision DESC);
CREATE TABLE launch_attempts (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    definition_id TEXT NOT NULL,
    request_key TEXT NOT NULL,
    attempt_json TEXT NOT NULL,
    UNIQUE(definition_id,request_key)
);
"""


def head(
    connection: sqlite3.Connection, target: LaunchDraftTarget
) -> LaunchDraftRecord | None:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT draft_json FROM launch_drafts WHERE workspace_id=? "
            "AND experiment=? "
            "AND state!='conflict' ORDER BY revision DESC LIMIT 1",
            (str(target.workspace_id), target.experiment),
        ).fetchone(),
    )
    return (
        None
        if row is None
        else LaunchDraftRecord.model_validate_json(cast("str", row[0]))
    )


def save(connection: sqlite3.Connection, command: LaunchDraftSave) -> LaunchDraftView:
    replay = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT command_json,draft_json FROM launch_drafts WHERE operation_id=?",
            (str(command.operation_id),),
        ).fetchone(),
    )
    if replay is not None:
        if LaunchDraftSave.model_validate_json(cast("str", replay[0])) != command:
            raise BackendConflict("Editing operation identity was reused")
        return LaunchDraftView(
            head=head(connection, command.target),
            saved=LaunchDraftRecord.model_validate_json(cast("str", replay[1])),
        )
    current = head(connection, command.target)
    state = (
        "conflict"
        if command.expected_revision != (current.revision if current else 0)
        else "discarded"
        if command.discard
        else "saved"
    )
    cursor = connection.execute(
        "INSERT INTO "
        "launch_drafts(operation_id,workspace_id,experiment,state,"
        "command_json,draft_json) VALUES(?,?,?,?,?,'{}')",
        (
            str(command.operation_id),
            str(command.target.workspace_id),
            command.target.experiment,
            state,
            command.model_dump_json(),
        ),
    )
    assert cursor.lastrowid is not None
    saved = LaunchDraftRecord(
        revision=cursor.lastrowid,
        target=command.target,
        input=command.input,
        state=state,
        created_at=datetime.now(UTC),
    )
    connection.execute(
        "UPDATE launch_drafts SET draft_json=? WHERE revision=?",
        (saved.model_dump_json(), saved.revision),
    )
    return LaunchDraftView(head=head(connection, command.target), saved=saved)


def history(
    connection: sqlite3.Connection, before: int | None, limit: int
) -> LaunchDraftPage:
    rows = cast(
        "list[sqlite3.Row]",
        connection.execute(
            "SELECT draft_json FROM launch_drafts WHERE (? IS NULL OR revision<?) "
            "ORDER BY revision DESC LIMIT ?",
            (before, before, limit + 1),
        ).fetchall(),
    )
    items = [
        LaunchDraftRecord.model_validate_json(cast("str", row[0]))
        for row in rows[:limit]
    ]
    return LaunchDraftPage(
        items=items, next_cursor=items[-1].revision if len(rows) > limit else None
    )


def retain(
    connection: sqlite3.Connection, command: LaunchAttemptSave
) -> LaunchAttemptRecord:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT attempt_json FROM launch_attempts WHERE definition_id=? AND "
            "request_key=?",
            (command.definition.id, command.request.request_key),
        ).fetchone(),
    )
    if row is not None:
        existing = LaunchAttemptRecord.model_validate_json(cast("str", row[0]))
        if (
            existing.definition != command.definition
            or existing.request != command.request
        ):
            raise BackendConflict(
                "Original request identity belongs to different input"
            )
        return existing
    cursor = connection.execute(
        "INSERT INTO launch_attempts(definition_id,request_key,attempt_json) "
        "VALUES(?,?,'{}')",
        (command.definition.id, command.request.request_key),
    )
    assert cursor.lastrowid is not None
    saved = LaunchAttemptRecord(
        definition=command.definition,
        request=command.request,
        sequence=cursor.lastrowid,
        created_at=datetime.now(UTC),
    )
    connection.execute(
        "UPDATE launch_attempts SET attempt_json=? WHERE sequence=?",
        (saved.model_dump_json(), saved.sequence),
    )
    return saved


def attempt(connection: sqlite3.Connection, sequence: int) -> LaunchAttemptRecord:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT attempt_json FROM launch_attempts WHERE sequence=?", (sequence,)
        ).fetchone(),
    )
    if row is None:
        raise BackendNotFound("Original submission was not found")
    return LaunchAttemptRecord.model_validate_json(cast("str", row[0]))


def attempts(
    connection: sqlite3.Connection, before: int | None, limit: int
) -> LaunchAttemptPage:
    rows = cast(
        "list[sqlite3.Row]",
        connection.execute(
            "SELECT attempt_json FROM launch_attempts WHERE (? IS NULL OR "
            "sequence<?) ORDER BY sequence DESC LIMIT ?",
            (before, before, limit + 1),
        ).fetchall(),
    )
    items = [
        LaunchAttemptRecord.model_validate_json(cast("str", row[0]))
        for row in rows[:limit]
    ]
    return LaunchAttemptPage(
        items=items, next_cursor=items[-1].sequence if len(rows) > limit else None
    )
