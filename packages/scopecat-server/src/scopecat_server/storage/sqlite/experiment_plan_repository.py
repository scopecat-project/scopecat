"""Content-addressed plan revisions with optimistic edits and reversible hiding."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.experiment_plan import (
    ExperimentPlanList,
    ExperimentPlanRevision,
    ExperimentPlanSave,
)
from scopecat.records.plan_ref import ExperimentPlanRef

from scopecat_server.errors import BackendConflict, BackendNotFound

if TYPE_CHECKING:
    from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


class ExperimentPlanRepository:
    def __init__(self, store: SQLiteProjectStore) -> None:
        self.store = store

    def get(self, ref: ExperimentPlanRef) -> ExperimentPlanRevision:
        with self.store.sqlite.read_connection() as connection:
            return self.get_in_transaction(connection, ref)

    def get_in_transaction(
        self, connection: sqlite3.Connection, ref: ExperimentPlanRef
    ) -> ExperimentPlanRevision:
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                "SELECT digest, content_hash FROM experiment_plan_revisions "
                "WHERE plan_id=? AND revision=?",
                (ref.plan_id, ref.revision),
            ).fetchone(),
        )
        if row is None:
            raise BackendNotFound("experiment plan revision was not found")
        if row["content_hash"] != ref.content_hash:
            raise BackendConflict("experiment plan reference hash does not match")
        item = ExperimentPlanRevision.model_validate_json(
            self.store.objects.read(cast("str", row["digest"]))
        )
        if (
            item.ref != ref
            or sha256_json_hash(item.model_dump(mode="json", exclude={"ref"}))
            != ref.content_hash
        ):
            raise BackendConflict("experiment plan content identity does not match")
        return item

    def list(self, *, plan_id: str | None = None) -> ExperimentPlanList:
        with self.store.sqlite.read_connection() as connection:
            if plan_id is None:
                rows = cast(
                    "list[sqlite3.Row]",
                    connection.execute(
                        "SELECT "
                        "r.plan_id,r.revision,r.content_hash "
                        "FROM experiment_plan_revisions r "
                        "JOIN experiment_plan_heads h ON "
                        "h.plan_id=r.plan_id AND h.revision=r.revision "
                        "WHERE h.hidden=0 ORDER BY r.plan_id"
                    ).fetchall(),
                )
            else:
                rows = cast(
                    "list[sqlite3.Row]",
                    connection.execute(
                        "SELECT plan_id,revision,content_hash "
                        "FROM experiment_plan_revisions "
                        "WHERE plan_id=? ORDER BY revision DESC",
                        (plan_id,),
                    ).fetchall(),
                )
        return ExperimentPlanList(
            items=tuple(
                self.get(
                    ExperimentPlanRef(
                        plan_id=cast("str", row["plan_id"]),
                        revision=cast("int", row["revision"]),
                        content_hash=cast("str", row["content_hash"]),
                    )
                )
                for row in rows
            )
        )

    def save(self, command: ExperimentPlanSave) -> ExperimentPlanRevision:
        parent = command.previous or command.copied_from
        if parent is not None:
            self.get(parent)
        plan_id = (
            command.previous.plan_id if command.previous else f"plan_{uuid4().hex}"
        )
        revision = command.previous.revision + 1 if command.previous else 1
        values = command.model_dump(mode="json") | {
            "saved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")
        }
        ref = ExperimentPlanRef(
            plan_id=plan_id, revision=revision, content_hash=sha256_json_hash(values)
        )
        item = ExperimentPlanRevision.model_validate_json(
            json.dumps(values | {"ref": ref.model_dump(mode="json")})
        )
        digest = self.store.objects.put(item.model_dump_json().encode("utf-8")).digest
        with self.store.sqlite.write_transaction() as connection:
            if command.previous:
                cursor = connection.execute(
                    "UPDATE experiment_plan_heads SET revision=?, "
                    "hidden=0 WHERE plan_id=? AND revision=? AND hidden=0",
                    (revision, plan_id, command.previous.revision),
                )
                if cursor.rowcount != 1:
                    raise BackendConflict(
                        "plan was edited or deleted; reopen its "
                        "latest revision before editing"
                    )
            else:
                connection.execute(
                    "INSERT INTO experiment_plan_heads VALUES (?, ?, 0)",
                    (plan_id, revision),
                )
            connection.execute(
                "INSERT INTO experiment_plan_revisions VALUES (?, ?, ?, ?)",
                (plan_id, revision, ref.content_hash, digest),
            )
        return item

    def hide(self, ref: ExperimentPlanRef) -> None:
        self.get(ref)
        with self.store.sqlite.write_transaction() as connection:
            cursor = connection.execute(
                "UPDATE experiment_plan_heads SET hidden=1 WHERE "
                "plan_id=? AND revision=?",
                (ref.plan_id, ref.revision),
            )
            if cursor.rowcount != 1:
                raise BackendConflict(
                    "plan changed; reopen its latest revision before deleting"
                )
