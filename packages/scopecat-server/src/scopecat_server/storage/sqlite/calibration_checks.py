"""Indexed declaration projection; procedure intent remains authoritative."""

import sqlite3
from typing import cast

from scopecat.automation import ProcedureRun
from scopecat.daemon.calibration_checks import (
    CalibrationCheckPage,
    CalibrationCheckQuery,
)
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.calibration_check import CalibrationCheckRequest

from scopecat_server.storage.sqlite.connection import SQLiteDatabase


def index_check(connection: sqlite3.Connection, run: ProcedureRun) -> None:
    if "calibration_check" not in run.intent:
        return
    declaration = CalibrationCheckRequest.model_validate(
        run.intent["calibration_check"]
    )
    content: dict[str, object] = declaration.model_dump(mode="json")
    connection.execute(
        """
        INSERT INTO calibration_check_requests(sequence, scope_hash, context_hash)
        SELECT sequence, ?, ? FROM procedure_runs WHERE procedure_run_id = ?
        """,
        (
            sha256_json_hash(content["scope"]),
            sha256_json_hash(content["context"]),
            run.procedure_run_id,
        ),
    )


class CalibrationCheckStore:
    def __init__(self, sqlite: SQLiteDatabase) -> None:
        self._sqlite = sqlite

    def query(self, query: CalibrationCheckQuery) -> CalibrationCheckPage:
        clauses: list[str] = []
        parameters: list[str | int] = []
        content: dict[str, object] = query.model_dump(mode="json")
        for field in ("scope", "context"):
            if content[field] is not None:
                clauses.append(f"checks.{field}_hash = ?")
                parameters.append(sha256_json_hash(content[field]))
        if query.cursor is not None:
            clauses.append("checks.sequence < ?")
            parameters.append(query.cursor)
        where = "" if not clauses else f"WHERE {' AND '.join(clauses)}"
        parameters.append(query.limit + 1)
        with self._sqlite.read_connection() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    f"""
                SELECT checks.sequence, runs.run_json
                FROM calibration_check_requests AS checks
                JOIN procedure_runs AS runs ON runs.sequence = checks.sequence
                {where}
                ORDER BY checks.sequence DESC LIMIT ?
                """,  # noqa: S608 - fixed internal clauses; values are bound
                    parameters,
                ).fetchall(),
            )
        selected = rows[: query.limit]
        return CalibrationCheckPage(
            items=tuple(
                ProcedureRun.model_validate_json(cast("str", row["run_json"]))
                for row in selected
            ),
            next_cursor=cast("int", selected[-1]["sequence"])
            if len(rows) > query.limit
            else None,
        )
