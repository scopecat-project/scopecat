"""Capture retained scientific evidence through an existing read transaction."""

import sqlite3
from collections.abc import Iterator
from typing import cast

from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.content import ContentEntry
from scopecat.records.exchange import RunEvidence
from scopecat.records.run_request import RunRequest
from scopecat.runs.refs import CONFIG_PROFILE_SNAPSHOT_REF, RUN_REQUEST_REF

from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


def capture_run_evidence(
    connection: sqlite3.Connection, runs: SQLiteRunRepository, run_id: str
) -> RunEvidence:
    """Read accepted run evidence without opening an execution environment.

    Keep this transaction open while capturing referenced revisions, measurements
    and artifacts, so the eventual exchange has one common capture boundary.
    """
    snapshot = runs.read_snapshot_in_transaction(connection, run_id)
    row = cast(
        "sqlite3.Row",
        connection.execute(
            "SELECT identity FROM project_identity WHERE singleton=1"
        ).fetchone(),
    )
    rows = cast(
        "Iterator[sqlite3.Row]",
        connection.execute(
            "SELECT entry_json FROM run_contents WHERE run_id=? "
            "ORDER BY role, content_id",
            (run_id,),
        ),
    )
    return RunEvidence(
        source_project_id=cast("str", row[0]),
        snapshot=snapshot,
        request=RunRequest.model_validate_json(
            runs.read_bytes_in_transaction(
                connection,
                run_id,
                RUN_REQUEST_REF,
            )
        ),
        configuration=ConfigProfileSnapshot.model_validate_json(
            runs.read_bytes_in_transaction(
                connection, run_id, CONFIG_PROFILE_SNAPSHOT_REF
            )
        ),
        contents=tuple(
            ContentEntry.model_validate_json(cast("str", item[0])) for item in rows
        ),
    )
