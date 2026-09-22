"""Branch generations and immutable commit receipts in the current store."""

import sqlite3
from typing import cast

from scopecat.records.parameter_branch import ParameterBranch


class ParameterBranchRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def history(self, name: str) -> tuple[ParameterBranch, ...]:
        rows = cast(
            "list[sqlite3.Row]",
            self.connection.execute(
                "SELECT record_json FROM parameter_branch_commits "
                "WHERE name = ? ORDER BY generation DESC",
                (name,),
            ).fetchall(),
        )
        return tuple(
            ParameterBranch.model_validate_json(cast("str", row[0])) for row in rows
        )

    def heads(self, *, limit: int, after: str | None) -> tuple[ParameterBranch, ...]:
        rows = cast(
            "list[sqlite3.Row]",
            self.connection.execute(
                "SELECT c.record_json FROM parameter_branch_commits AS c "
                "JOIN (SELECT name, MAX(generation) AS generation "
                "FROM parameter_branch_commits WHERE (? IS NULL OR name > ?) "
                "GROUP BY name ORDER BY name LIMIT ?) AS h "
                "ON c.name = h.name AND c.generation = h.generation ORDER BY c.name",
                (after, after, limit),
            ).fetchall(),
        )
        return tuple(
            ParameterBranch.model_validate_json(cast("str", row[0])) for row in rows
        )

    def get(self, name: str) -> ParameterBranch:
        row = cast(
            "sqlite3.Row | None",
            self.connection.execute(
                "SELECT record_json FROM parameter_branch_commits "
                "WHERE name = ? ORDER BY generation DESC LIMIT 1",
                (name,),
            ).fetchone(),
        )
        if row is None:
            raise KeyError(name)
        return ParameterBranch.model_validate_json(cast("str", row[0]))

    def replay(self, name: str, generation: int, intent: str) -> ParameterBranch | None:
        row = cast(
            "sqlite3.Row | None",
            self.connection.execute(
                "SELECT record_json, intent_hash FROM parameter_branch_commits "
                "WHERE name = ? AND generation = ?",
                (name, generation),
            ).fetchone(),
        )
        if row is None:
            return None
        if row[1] != intent:
            raise ValueError("parameter branch changed; reload before saving")
        return ParameterBranch.model_validate_json(cast("str", row[0]))

    def append(self, branch: ParameterBranch, intent: str) -> None:
        self.connection.execute(
            "INSERT INTO parameter_branch_commits "
            "(name, generation, revision_id, record_json, intent_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                branch.name,
                branch.generation,
                branch.revision.revision_id,
                branch.model_dump_json(),
                intent,
            ),
        )
