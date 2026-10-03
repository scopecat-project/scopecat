"""Preview and recoverable removal of explicitly selected scientific records."""

import hashlib
import json
import shutil
import sqlite3
from collections.abc import Callable
from threading import Lock
from typing import cast
from uuid import NAMESPACE_URL, uuid5

from scopecat.records.data_cleanup import (
    DataCleanupBlocker,
    DataCleanupCommand,
    DataCleanupOperation,
    DataCleanupPreview,
    DataCleanupSelection,
)
from scopecat.records.practice import PracticeResource

from scopecat_server.storage.sqlite.data_cleanup import delete_selected_records
from scopecat_server.storage.sqlite.data_dependencies import (
    retained_dependencies,
    selection_resources,
)
from scopecat_server.storage.sqlite.practice import PracticeOwnership
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.resource_objects import resource_directory


class DataCleanupService:
    def __init__(self, store: SQLiteProjectStore):
        self.store = store
        self._lock = Lock()

    def preview(self, selection: DataCleanupSelection) -> DataCleanupPreview:
        with self.store.sqlite.read_transaction() as connection:
            return self._preview(connection, selection)

    def _preview(
        self, connection: sqlite3.Connection, selection: DataCleanupSelection
    ) -> DataCleanupPreview:
        blockers = list(
            retained_dependencies(connection, self.store.objects, selection)
        )
        owners = PracticeOwnership(connection)
        evidence: list[object] = []
        size = 0
        for kind, identity in selection_resources(selection):
            owner = owners.owner(cast("PracticeResource", kind), identity)
            scope = owners.get(owner) if owner is not None else None
            clearing_practice = (
                scope is not None
                and scope.state == "cleaning"
                and scope.workers_retired
            )
            if scope is not None and not clearing_practice:
                blockers.append(
                    DataCleanupBlocker(
                        owner=f"practice:{scope.id}",
                        reason=(
                            "Clear this practice from Help to settle its tasks "
                            "and choose whether to keep its edited files"
                        ),
                    )
                )
            table, column = {
                "run": ("runs", "run_id"),
                "analysis": ("analysis_publications", "record_id"),
                "procedure": ("procedure_runs", "procedure_run_id"),
                "setup": ("setup_revisions", "revision_id"),
                "setup_definition": ("setup_definitions", "definition_id"),
                "parameters": ("parameter_revisions", "revision_id"),
                "capture": ("imported_captures", "content_hash"),
            }[kind]
            row = cast(
                "sqlite3.Row | None",
                connection.execute(
                    f"SELECT * FROM {table} WHERE {column}=?",  # noqa: S608 - fixed schema identifiers
                    (identity,),
                ).fetchone(),
            )
            if row is None and not clearing_practice:
                blockers.append(
                    DataCleanupBlocker(
                        owner=f"{kind}:{identity}",
                        reason="Record no longer exists; refresh your selection",
                    )
                )
            elif row is not None:
                evidence.append(tuple(row))
                if kind == "analysis" and row["run_id"] is not None:
                    blockers.append(
                        DataCleanupBlocker(
                            owner=f"analysis:{identity}",
                            reason=(
                                "This analysis belongs to a measurement; "
                                "select that measurement for cleanup"
                            ),
                        )
                    )
            if kind in {"run", "analysis", "capture"}:
                directory = resource_directory(self.store.objects, kind, identity)
                size += sum(
                    path.stat().st_size
                    for path in directory.rglob("*")
                    if path.is_file() and not path.is_symlink()
                )
            if kind == "run":
                evidence.extend(
                    tuple(item)
                    for item in cast(
                        "list[sqlite3.Row]",
                        connection.execute(
                            "SELECT ref, digest FROM run_repository_refs "
                            "WHERE run_id=? ORDER BY ref",
                            (identity,),
                        ).fetchall(),
                    )
                )
            if kind in {"run", "procedure"} and not clearing_practice:
                table, column = (
                    ("scheduler_runs", "run_id")
                    if kind == "run"
                    else ("procedure_runs", "procedure_run_id")
                )
                row = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        f"SELECT state FROM {table} WHERE {column}=?",  # noqa: S608 - fixed schema identifiers
                        (identity,),
                    ).fetchone(),
                )
                if row is not None and row[0] != "closed":
                    blockers.append(
                        DataCleanupBlocker(
                            owner=f"{kind}:{identity}",
                            reason=(
                                "Finish or cancel this task through its normal "
                                "task controls before clearing data"
                            ),
                        )
                    )
        encoded = (
            selection.model_dump_json()
            + "\n"
            + "\n".join(item.model_dump_json() for item in blockers)
            + json.dumps(evidence)
        )
        return DataCleanupPreview(
            selection=selection,
            fingerprint=hashlib.sha256(encoded.encode()).hexdigest(),
            blockers=tuple(blockers),
            record_count=sum(1 for _ in selection_resources(selection)),
            bytes_to_reclaim=size,
        )

    def operations(self) -> tuple[DataCleanupOperation, ...]:
        with self.store.sqlite.read_transaction() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT record_json FROM data_cleanup_operations "
                    "ORDER BY rowid DESC"
                ).fetchall(),
            )
            return tuple(
                DataCleanupOperation.model_validate_json(cast("str", row[0]))
                for row in rows
            )

    @staticmethod
    def _save(connection: sqlite3.Connection, operation: DataCleanupOperation) -> None:
        connection.execute(
            "INSERT INTO data_cleanup_operations VALUES (?,?) "
            "ON CONFLICT(operation_id) DO UPDATE SET record_json=excluded.record_json",
            (operation.id, operation.model_dump_json()),
        )

    def execute(
        self,
        command: DataCleanupCommand,
        *,
        settle: Callable[[str], None] | None = None,
    ) -> DataCleanupOperation:
        identity = uuid5(NAMESPACE_URL, f"scopecat-cleanup:{command.request_key}").hex
        with self._lock:
            with self.store.sqlite.write_transaction() as connection:
                row = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        "SELECT record_json FROM data_cleanup_operations "
                        "WHERE operation_id=?",
                        (identity,),
                    ).fetchone(),
                )
                if row is not None:
                    operation = DataCleanupOperation.model_validate_json(
                        cast("str", row[0])
                    )
                    if operation.selection != command.preview.selection:
                        raise ValueError(
                            "Cleanup request key already belongs to "
                            "a different selection"
                        )
                else:
                    preview = self._preview(connection, command.preview.selection)
                    if preview.fingerprint != command.preview.fingerprint:
                        raise ValueError(
                            "Data or retaining dependencies changed; "
                            "preview this cleanup again"
                        )
                    if preview.blockers:
                        raise ValueError(
                            "Resolve retaining dependencies and active tasks "
                            "before clearing data"
                        )
                    if not preview.record_count:
                        raise ValueError("Select records to clear")
                    operation = DataCleanupOperation(
                        id=identity, selection=preview.selection
                    )
                    self._save(connection, operation)
                    for kind, resource_id in selection_resources(operation.selection):
                        connection.execute(
                            "INSERT OR IGNORE INTO deleted_resources VALUES (?,?,?)",
                            (kind, resource_id, operation.id),
                        )
            return self._resume(operation, settle=settle)

    def resume(
        self, identity: str, *, settle: Callable[[str], None] | None = None
    ) -> DataCleanupOperation:
        with self._lock:
            with self.store.sqlite.read_transaction() as connection:
                row = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        "SELECT record_json FROM data_cleanup_operations "
                        "WHERE operation_id=?",
                        (identity,),
                    ).fetchone(),
                )
                if row is None:
                    raise KeyError(identity)
                operation = DataCleanupOperation.model_validate_json(
                    cast("str", row[0])
                )
            return self._resume(operation, settle=settle)

    def _resume(
        self, operation: DataCleanupOperation, *, settle: Callable[[str], None] | None
    ) -> DataCleanupOperation:
        if operation.state == "complete":
            return operation
        try:
            if operation.state == "prepared":
                for procedure in operation.selection.procedures:
                    if settle is None:
                        raise ValueError(
                            "Cleanup needs the application's task worker owner"
                        )
                    settle(procedure)
                with self.store.sqlite.write_transaction() as connection:
                    # Preparation checked dependencies and committed write fences
                    # atomically. Retries need no repeated scan of retained data.
                    delete_selected_records(connection, operation.selection)
                    operation = operation.model_copy(
                        update={"state": "records_removed", "error": None}
                    )
                    self._save(connection, operation)
            for kind, identity in selection_resources(operation.selection):
                if kind in {"run", "analysis", "capture"}:
                    directory = resource_directory(self.store.objects, kind, identity)
                    if directory.exists():
                        shutil.rmtree(directory)
            operation = operation.model_copy(
                update={"state": "complete", "error": None}
            )
        except Exception as error:
            operation = operation.model_copy(update={"error": str(error)})
        with self.store.sqlite.write_transaction() as connection:
            if operation.state == "complete":
                # Imported files may be explicitly opened again after removal.
                # Keep the fence until owned bytes have been removed successfully.
                connection.executemany(
                    "DELETE FROM deleted_resources "
                    "WHERE kind='capture' AND resource_id=? AND operation_id=?",
                    [
                        (identity, operation.id)
                        for identity in operation.selection.captures
                    ],
                )
            self._save(connection, operation)
        return operation
