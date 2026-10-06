"""Parameter editing history, owned by the application database."""

import sqlite3
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from scopecat_server.parameter_drafts import ParameterDraft, ParameterDraftPage

PARAMETER_DRAFT_TABLES_SQL = """
CREATE TABLE parameter_drafts (
    revision INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL,
    base_id TEXT NOT NULL,
    working_branch TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('saved','conflict','discarded','completed')),
    draft_json TEXT NOT NULL
);
CREATE INDEX parameter_drafts_identity ON parameter_drafts(draft_id, revision DESC);
CREATE INDEX parameter_drafts_base ON parameter_drafts(base_id, revision DESC);
"""


def head(connection: sqlite3.Connection, draft_id: UUID) -> ParameterDraft | None:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT draft_json FROM parameter_drafts WHERE draft_id=? AND "
            "state!='conflict' "
            "ORDER BY revision DESC LIMIT 1",
            (str(draft_id),),
        ).fetchone(),
    )
    return (
        None if row is None else ParameterDraft.model_validate_json(cast("str", row[0]))
    )


def active(
    connection: sqlite3.Connection, base_id: str, working_branch: str
) -> ParameterDraft | None:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT d.draft_json FROM parameter_drafts d WHERE ((?!='' AND "
            "d.working_branch=?) OR (?='' AND d.working_branch='' AND d.base_id=?)) "
            "AND d.state='saved' "
            "AND NOT EXISTS (SELECT 1 FROM parameter_drafts n WHERE "
            "n.draft_id=d.draft_id "
            "AND n.state!='conflict' AND n.revision>d.revision) ORDER BY d.revision "
            "DESC LIMIT 1",
            (working_branch, working_branch, working_branch, base_id),
        ).fetchone(),
    )
    return (
        None if row is None else ParameterDraft.model_validate_json(cast("str", row[0]))
    )


def append(connection: sqlite3.Connection, draft: ParameterDraft) -> ParameterDraft:
    cursor = connection.execute(
        "INSERT INTO "
        "parameter_drafts(draft_id,base_id,working_branch,state,draft_json) "
        "VALUES (?,?,?,?,'{}')",
        (
            str(draft.draft_id),
            draft.base.revision_id,
            draft.working_branch,
            draft.state,
        ),
    )
    assert cursor.lastrowid is not None
    saved = draft.model_copy(
        update={"revision": cursor.lastrowid, "created_at": datetime.now(UTC)}
    )
    connection.execute(
        "UPDATE parameter_drafts SET draft_json=? WHERE revision=?",
        (saved.model_dump_json(), saved.revision),
    )
    return saved


def history(
    connection: sqlite3.Connection, base_id: str, before: int | None, limit: int
) -> ParameterDraftPage:
    rows = cast(
        "list[sqlite3.Row]",
        connection.execute(
            "SELECT draft_json FROM parameter_drafts WHERE base_id=? AND (? IS NULL "
            "OR revision<?) "
            "ORDER BY revision DESC LIMIT ?",
            (base_id, before, before, limit + 1),
        ).fetchall(),
    )
    items = [
        ParameterDraft.model_validate_json(cast("str", row[0])) for row in rows[:limit]
    ]
    return ParameterDraftPage(
        items=items, next_cursor=items[-1].revision if len(rows) > limit else None
    )


def revision(
    connection: sqlite3.Connection, draft_id: UUID, revision: int
) -> ParameterDraft | None:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT draft_json FROM parameter_drafts WHERE draft_id=? AND revision=?",
            (str(draft_id), revision),
        ).fetchone(),
    )
    return (
        None if row is None else ParameterDraft.model_validate_json(cast("str", row[0]))
    )
