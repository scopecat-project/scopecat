"""Independent immutable parameter revisions in the current-format store."""

import sqlite3
from typing import cast

from scopecat.records.parameter_revision import ParameterRevision


class ParameterRevisionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, revision_id: str) -> ParameterRevision:
        row = cast(
            "sqlite3.Row | None",
            self.connection.execute(
                "SELECT record_json FROM parameter_revisions WHERE revision_id = ?",
                (revision_id,),
            ).fetchone(),
        )
        if row is None:
            raise KeyError(revision_id)
        return ParameterRevision.model_validate_json(cast("str", row[0]))

    def list(self) -> tuple[ParameterRevision, ...]:
        rows = cast(
            "list[sqlite3.Row]",
            self.connection.execute(
                "SELECT record_json FROM parameter_revisions ORDER BY rowid DESC"
            ).fetchall(),
        )
        return tuple(
            ParameterRevision.model_validate_json(cast("str", row[0])) for row in rows
        )

    def save(self, revision: ParameterRevision) -> ParameterRevision:
        try:
            existing = self.get(revision.id)
        except KeyError:
            existing = None
        if existing is not None:
            if existing.model_dump(exclude={"recorded_at"}) != revision.model_dump(
                exclude={"recorded_at"}
            ):
                raise ValueError(
                    "parameter revision ID already has different content or provenance"
                )
            return existing
        self.connection.execute(
            "INSERT INTO parameter_revisions(revision_id, record_json) VALUES (?, ?)",
            (revision.id, revision.model_dump_json()),
        )
        return revision
