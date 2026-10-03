"""Common record removal for an explicitly admitted, quiescent selection.

This layer owns relational deletion order. It does not authorize deletion,
expand the selection, stop processes or decide which files may be discarded.
"""

import sqlite3

from scopecat.records.data_cleanup import DataCleanupSelection

from scopecat_server.errors import BackendConflict


def require_retained_references(connection: sqlite3.Connection, content: str) -> None:
    if (
        connection.execute(
            "SELECT 1 FROM json_tree(?) j JOIN deleted_resources d "
            "ON d.resource_id=j.atom WHERE j.type='text' LIMIT 1",
            (content,),
        ).fetchone()
        is not None
    ):
        raise BackendConflict("Scientific reference targets cleared data")


def require_retained_resource(
    connection: sqlite3.Connection, kind: str, identity: str
) -> None:
    if (
        connection.execute(
            "SELECT 1 FROM deleted_resources WHERE kind=? AND resource_id=?",
            (kind, identity),
        ).fetchone()
        is not None
    ):
        raise BackendConflict("This record is being cleared or has been cleared")


def delete_selected_records(
    connection: sqlite3.Connection, selection: DataCleanupSelection
) -> None:
    for capture in selection.captures:
        connection.execute(
            "DELETE FROM imported_run_identities WHERE capture_hash=?", (capture,)
        )
        connection.execute(
            "DELETE FROM imported_captures WHERE content_hash=?", (capture,)
        )
    for run_id in selection.runs:
        # Measurement projections reference append rows and recovery groups;
        # remove these indexes before their scheduler and run parents.
        for table in (
            "execution_measurement_projection",
            "execution_measurement_seals",
            "execution_measurement_records",
            "execution_measurement_appends",
            "execution_measurement_fragments",
            "execution_measurement_headers",
            "run_addresses",
            "run_deployments",
            "run_sample_batches",
            "research_runs",
            "durable_events",
        ):
            connection.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))  # noqa: S608 - fixed internal table names
        connection.execute(
            "DELETE FROM resource_claims WHERE owner_kind='run' AND owner_id=?",
            (run_id,),
        )
        connection.execute("DELETE FROM scheduler_runs WHERE run_id=?", (run_id,))
        connection.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
    for analysis in selection.analyses:
        connection.execute(
            "DELETE FROM analysis_publications WHERE record_id=?", (analysis,)
        )
    for procedure in selection.procedures:
        connection.execute(
            "DELETE FROM procedure_runs WHERE procedure_run_id=?", (procedure,)
        )
    for setup in selection.setups:
        connection.execute("DELETE FROM setup_revisions WHERE revision_id=?", (setup,))
    for definition in selection.setup_definitions:
        connection.execute(
            "DELETE FROM setup_definitions WHERE definition_id=?", (definition,)
        )
    for parameters in selection.parameters:
        connection.execute(
            "DELETE FROM parameter_revisions WHERE revision_id=?", (parameters,)
        )
