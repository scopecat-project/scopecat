"""Immutable laboratory capability requirements stored with the current database."""

import sqlite3
from typing import cast

from scopecat.daemon.calibration_checks import CalibrationProfilePage
from scopecat.records.calibration_policy import CalibrationProfileRecord

from scopecat_server.storage.sqlite.practice import PracticeOwnership


class CalibrationProfileStore:
    def read(
        self, connection: sqlite3.Connection, identity: str
    ) -> CalibrationProfileRecord | None:
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                "SELECT record_json FROM calibration_profiles WHERE profile_id = ?",
                (identity,),
            ).fetchone(),
        )
        return (
            None
            if row is None
            else CalibrationProfileRecord.model_validate_json(cast("str", row[0]))
        )

    def insert(
        self, connection: sqlite3.Connection, record: CalibrationProfileRecord
    ) -> None:
        PracticeOwnership(connection).claim_created(
            "calibration_profile", record.profile.id
        )
        connection.execute(
            "INSERT INTO calibration_profiles(profile_id, record_json) VALUES (?, ?)",
            (record.profile.id, record.model_dump_json()),
        )

    def list(
        self, connection: sqlite3.Connection, limit: int, cursor: int | None
    ) -> CalibrationProfilePage:
        scope = PracticeOwnership(connection).workspace_scope()
        where = (
            "WHERE (SELECT scope_id FROM practice_resources "
            "WHERE kind='calibration_profile' "
            "AND resource_id=calibration_profiles.profile_id) IS ?"
        )
        if cursor is not None:
            where += " AND sequence < ?"
        parameters = (
            (scope, limit + 1) if cursor is None else (scope, cursor, limit + 1)
        )
        rows = cast(
            "list[sqlite3.Row]",
            connection.execute(
                "SELECT sequence, record_json FROM calibration_profiles "  # noqa: S608 - fixed fragments
                f"{where} ORDER BY sequence DESC LIMIT ?",
                parameters,
            ).fetchall(),
        )
        selected = rows[:limit]
        return CalibrationProfilePage(
            items=tuple(
                CalibrationProfileRecord.model_validate_json(
                    cast("str", row["record_json"])
                )
                for row in selected
            ),
            next_cursor=cast("int", selected[-1]["sequence"])
            if len(rows) > limit
            else None,
        )
