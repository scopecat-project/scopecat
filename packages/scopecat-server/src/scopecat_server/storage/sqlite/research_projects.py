"""Research project names and explicit many-to-many evidence associations."""

import sqlite3
from datetime import UTC, datetime
from typing import Literal, cast

from scopecat.records.research_project import (
    ResearchMemberPage,
    ResearchProject,
    ResearchProjectEdit,
    ResearchProjectPage,
)

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.storage.sqlite.connection import SQLiteDatabase


def _project(row: sqlite3.Row) -> ResearchProject:
    return ResearchProject.model_validate(
        {
            "id": row["project_id"],
            **{
                key: row[key]
                for key in (
                    "revision",
                    "name",
                    "description",
                    "created_at",
                    "updated_at",
                )
            },
        }
    )


class ResearchProjectStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def list(
        self, *, limit: int = 100, before: int | None = None
    ) -> ResearchProjectPage:
        with self.database.read_connection() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT * FROM research_projects WHERE (? IS NULL OR sequence < ?) "
                    "ORDER BY sequence DESC LIMIT ?",
                    (before, before, limit + 1),
                ).fetchall(),
            )
        return ResearchProjectPage(
            items=tuple(_project(row) for row in rows[:limit]),
            next_cursor=cast("int", rows[limit - 1]["sequence"])
            if len(rows) > limit
            else None,
        )

    def save(self, project_id: str, edit: ResearchProjectEdit) -> ResearchProject:
        now = datetime.now(UTC).isoformat()
        with self.database.write_transaction() as connection:
            row = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT * FROM research_projects WHERE project_id=?", (project_id,)
                ).fetchone(),
            )
            if row is None:
                if edit.expected_revision != 0:
                    raise BackendConflict(
                        "research project no longer matches the selected revision"
                    )
                connection.execute(
                    "INSERT INTO research_projects(project_id,revision,name,"
                    "description,created_at,updated_at) "
                    "VALUES (?,1,?,?,?,?)",
                    (project_id, edit.name, edit.description, now, now),
                )
            else:
                if row["name"] == edit.name and row["description"] == edit.description:
                    return _project(row)
                if row["revision"] != edit.expected_revision:
                    raise BackendConflict(
                        "research project changed; reload before editing"
                    )
                connection.execute(
                    "UPDATE research_projects SET revision=revision+1,name=?,"
                    "description=?,updated_at=? "
                    "WHERE project_id=?",
                    (edit.name, edit.description, now, project_id),
                )
            result = cast(
                "sqlite3.Row",
                connection.execute(
                    "SELECT * FROM research_projects WHERE project_id=?", (project_id,)
                ).fetchone(),
            )
            return _project(result)

    def members(
        self,
        project_id: str,
        kind: Literal["samples", "runs"],
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> ResearchMemberPage:
        table, column = (
            ("research_samples", "sample_id")
            if kind == "samples"
            else ("research_runs", "run_id")
        )
        with self.database.read_transaction() as connection:
            self._require_project(connection, project_id)
            ids = tuple(
                cast("str", row[0])
                for row in cast(
                    "list[sqlite3.Row]",
                    connection.execute(
                        f"SELECT {column} FROM {table} WHERE project_id=? "  # noqa: S608
                        f"AND (? IS NULL OR {column}>?) ORDER BY {column} LIMIT ?",
                        (project_id, after, after, limit + 1),
                    ).fetchall(),
                )
            )
        return ResearchMemberPage(
            ids=ids[:limit], next_cursor=ids[limit - 1] if len(ids) > limit else None
        )

    def associate(
        self,
        project_id: str,
        kind: Literal["samples", "runs"],
        identity: str,
        *,
        present: bool,
    ) -> None:
        table, column, target = (
            ("research_samples", "sample_id", "samples")
            if kind == "samples"
            else ("research_runs", "run_id", "runs")
        )
        with self.database.write_transaction() as connection:
            self._require_project(connection, project_id)
            if (
                connection.execute(
                    f"SELECT 1 FROM {target} WHERE {column}=?",  # noqa: S608
                    (identity,),
                ).fetchone()
                is None
            ):
                raise BackendNotFound(f"unknown {kind} identity")
            if present:
                connection.execute(
                    f"INSERT OR IGNORE INTO {table}(project_id,{column}) "  # noqa: S608
                    "VALUES (?,?)",
                    (project_id, identity),
                )
            else:
                connection.execute(
                    f"DELETE FROM {table} WHERE project_id=? AND {column}=?",  # noqa: S608
                    (project_id, identity),
                )

    @staticmethod
    def _require_project(connection: sqlite3.Connection, project_id: str) -> None:
        if (
            connection.execute(
                "SELECT 1 FROM research_projects WHERE project_id=?", (project_id,)
            ).fetchone()
            is None
        ):
            raise BackendNotFound("research project not found")
