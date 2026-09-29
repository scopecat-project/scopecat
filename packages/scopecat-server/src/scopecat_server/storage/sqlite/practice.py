"""Transaction-local ownership shared by admission, writers and cleanup."""

import sqlite3
from collections.abc import Iterator
from typing import cast

from scopecat.records.practice import PracticeResource, PracticeScope


class PracticeOwnership:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, scope_id: str) -> PracticeScope:
        row = cast(
            "sqlite3.Row | None",
            self.connection.execute(
                "SELECT record_json FROM practice_scopes WHERE scope_id=?", (scope_id,)
            ).fetchone(),
        )
        if row is None:
            raise KeyError(scope_id)
        return PracticeScope.model_validate_json(cast("str", row[0]))

    def list(self) -> tuple[PracticeScope, ...]:
        return tuple(
            PracticeScope.model_validate_json(cast("str", row[0]))
            for row in cast(
                "Iterator[sqlite3.Row]",
                self.connection.execute(
                    "SELECT record_json FROM practice_scopes ORDER BY rowid DESC"
                ),
            )
        )

    def save(self, scope: PracticeScope) -> None:
        self.connection.execute(
            "INSERT INTO practice_scopes VALUES (?,?) "
            "ON CONFLICT(scope_id) DO UPDATE SET record_json=excluded.record_json",
            (scope.id, scope.model_dump_json()),
        )

    def require_active(self, scope_id: str) -> None:
        if self.get(scope_id).state != "active":
            raise ValueError(
                "Practice is being cleared or has been cleared; new writes are fenced"
            )

    def owner(self, kind: PracticeResource, resource_id: str) -> str | None:
        row = cast(
            "sqlite3.Row | None",
            self.connection.execute(
                "SELECT scope_id FROM practice_resources "
                "WHERE kind=? AND resource_id=?",
                (kind, resource_id),
            ).fetchone(),
        )
        return cast("str", row[0]) if row is not None else None

    def require_shared(self, *resources: tuple[PracticeResource, str]) -> str | None:
        """An ordinary/practice or cross-practice mixture cannot gain authority."""
        owners = {self.owner(kind, identity) for kind, identity in resources}
        if len(owners) > 1:
            raise ValueError(
                "Practice content cannot use ordinary or another practice's resources"
            )
        owner = next(iter(owners), None)
        if owner is not None:
            self.require_active(owner)
        return owner

    def claim(self, scope_id: str, kind: PracticeResource, resource_id: str) -> None:
        self.require_active(scope_id)
        existing = self.owner(kind, resource_id)
        if existing is not None and existing != scope_id:
            raise ValueError("Practice resource already belongs to another scope")
        self.connection.execute(
            "INSERT OR IGNORE INTO practice_resources VALUES (?,?,?)",
            (kind, resource_id, scope_id),
        )

    def resources(self, scope_id: str, kind: PracticeResource) -> tuple[str, ...]:
        return tuple(
            cast("str", row[0])
            for row in cast(
                "Iterator[sqlite3.Row]",
                self.connection.execute(
                    "SELECT resource_id FROM practice_resources "
                    "WHERE scope_id=? AND kind=? ORDER BY resource_id",
                    (scope_id, kind),
                ),
            )
        )
