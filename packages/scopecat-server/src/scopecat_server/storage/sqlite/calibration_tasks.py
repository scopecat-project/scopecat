"""Task specifications and exact stage associations in the procedure database."""

import sqlite3
from typing import cast

from scopecat.daemon.calibration_tasks import (
    CalibrationTaskListQuery,
    CalibrationTaskPage,
    CalibrationTaskRecord,
)


class CalibrationTaskStore:
    def read(
        self, connection: sqlite3.Connection, task_id: str
    ) -> CalibrationTaskRecord | None:
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                "SELECT record_json FROM calibration_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone(),
        )
        return (
            None
            if row is None
            else CalibrationTaskRecord.model_validate_json(
                cast("str", row["record_json"])
            )
        )

    def insert(
        self, connection: sqlite3.Connection, task: CalibrationTaskRecord
    ) -> None:
        connection.execute(
            "INSERT INTO calibration_tasks(task_id, record_json) VALUES (?, ?)",
            (task.specification.task_id, task.model_dump_json()),
        )

    def update(
        self, connection: sqlite3.Connection, task: CalibrationTaskRecord
    ) -> None:
        connection.execute(
            "UPDATE calibration_tasks SET record_json = ? WHERE task_id = ?",
            (task.model_dump_json(), task.specification.task_id),
        )

    def list(
        self, connection: sqlite3.Connection, query: CalibrationTaskListQuery
    ) -> CalibrationTaskPage:
        where = "" if query.cursor is None else "WHERE sequence < ?"
        parameters = (
            (query.limit + 1,)
            if query.cursor is None
            else (query.cursor, query.limit + 1)
        )
        rows = cast(
            "list[sqlite3.Row]",
            connection.execute(
                "SELECT sequence, record_json FROM calibration_tasks "  # noqa: S608 - fixed fragments
                f"{where} ORDER BY sequence DESC LIMIT ?",
                parameters,
            ).fetchall(),
        )
        selected = rows[: query.limit]
        return CalibrationTaskPage(
            items=tuple(
                CalibrationTaskRecord.model_validate_json(
                    cast("str", row["record_json"])
                )
                for row in selected
            ),
            next_cursor=cast("int", selected[-1]["sequence"])
            if len(rows) > query.limit
            else None,
        )
