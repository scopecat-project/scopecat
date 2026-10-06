"""Append-only Decision editing history in the application database."""

import sqlite3
from datetime import UTC, datetime
from typing import cast

from scopecat_server.decision_drafts import (
    DecisionDraft,
    DecisionDraftPage,
    DecisionDraftSave,
    DecisionDraftTarget,
)

DECISION_DRAFT_TABLES_SQL = """
CREATE TABLE decision_drafts (
    revision INTEGER PRIMARY KEY AUTOINCREMENT,
    procedure_run_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('saved', 'conflict', 'discarded')),
    draft_json TEXT NOT NULL
);
CREATE INDEX decision_drafts_target ON decision_drafts
    (procedure_run_id, step_key, attempt, revision DESC);
"""


def head(
    connection: sqlite3.Connection, target: DecisionDraftTarget
) -> DecisionDraft | None:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT draft_json FROM decision_drafts WHERE procedure_run_id=? "
            "AND step_key=? AND attempt=? AND state != 'conflict' "
            "ORDER BY revision DESC LIMIT 1",
            (target.procedure_run_id, target.step_key, target.attempt),
        ).fetchone(),
    )
    return (
        None if row is None else DecisionDraft.model_validate_json(cast("str", row[0]))
    )


def append(connection: sqlite3.Connection, command: DecisionDraftSave) -> DecisionDraft:
    current = head(connection, command.target)
    revision = 0 if current is None else current.revision
    state = (
        ("discarded" if command.discard else "saved")
        if revision == command.expected_revision
        else "conflict"
    )
    target = command.target
    cursor = connection.execute(
        "INSERT INTO decision_drafts "
        "(procedure_run_id, step_key, attempt, state, draft_json) "
        "VALUES (?, ?, ?, ?, '{}')",
        (target.procedure_run_id, target.step_key, target.attempt, state),
    )
    assert cursor.lastrowid is not None
    draft = DecisionDraft(
        revision=cursor.lastrowid,
        target=target,
        baseline=command.baseline,
        input=command.input,
        state=state,
        created_at=datetime.now(UTC),
    )
    connection.execute(
        "UPDATE decision_drafts SET draft_json=? WHERE revision=?",
        (draft.model_dump_json(), draft.revision),
    )
    return draft


def history(
    connection: sqlite3.Connection, *, before: int | None, limit: int
) -> DecisionDraftPage:
    rows = cast(
        "list[sqlite3.Row]",
        connection.execute(
            "SELECT draft_json FROM decision_drafts WHERE (? IS NULL OR revision < ?) "
            "ORDER BY revision DESC LIMIT ?",
            (before, before, limit + 1),
        ).fetchall(),
    )
    items = [
        DecisionDraft.model_validate_json(cast("str", row[0])) for row in rows[:limit]
    ]
    return DecisionDraftPage(
        items=items, next_cursor=items[-1].revision if len(rows) > limit else None
    )
