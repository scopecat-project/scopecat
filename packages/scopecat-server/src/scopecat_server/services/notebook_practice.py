"""Notebook lifecycle using the same ownership and conservative data cleanup."""

import sqlite3
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import cast
from uuid import NAMESPACE_URL, uuid5

from scopecat.records.data_cleanup import (
    DataCleanupCommand,
    DataCleanupPreview,
    DataCleanupSelection,
)
from scopecat.records.practice import NotebookPracticeClearCommand, PracticeScope

from scopecat_server.services.data_cleanup import DataCleanupService
from scopecat_server.storage.sqlite.practice import PracticeOwnership
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


class NotebookPracticeService:
    def __init__(self, store: SQLiteProjectStore, cleanup: DataCleanupService):
        self.store = store
        self.cleanup = cleanup
        self._lock = Lock()

    def enroll(self, workspace_id: str, directory: Path, lesson: str) -> PracticeScope:
        """The caller resolves this path from the registered workspace catalog.

        Only a fresh source may enroll. Existing unowned course records cannot be
        adopted retrospectively, including sources whose old receipt was lost.
        """
        identity = uuid5(NAMESPACE_URL, f"scopecat-course:{workspace_id}").hex
        with self._lock, self.store.sqlite.write_transaction() as connection:
            owners = PracticeOwnership(connection)
            existing = owners.owner("source", workspace_id)
            if existing is not None:
                scope = owners.get(existing)
                if scope.lesson != lesson or Path(scope.directory) != directory:
                    raise ValueError(
                        "Course registration does not match its retained source"
                    )
                return scope
            if (
                connection.execute(
                    "SELECT 1 FROM author_workspace_revisions WHERE "
                    "workspace_id=? LIMIT 1",
                    (workspace_id,),
                ).fetchone()
                is not None
            ):
                raise ValueError(
                    "This source already has unowned history; keep it for "
                    "manual cleanup"
                )
            scope = PracticeScope(
                id=identity,
                title=lesson,
                lesson=lesson,
                directory=str(directory),
                kind="notebook",
                workspace_id=workspace_id,
            )
            owners.save(scope)
            owners.claim(scope.id, "source", workspace_id)
            return scope

    @staticmethod
    def _scope(connection: sqlite3.Connection, identity: str) -> PracticeScope:
        scope = PracticeOwnership(connection).get(identity)
        if scope.kind != "notebook":
            raise ValueError("This lifecycle belongs to a registered Notebook course")
        return scope

    @staticmethod
    def _selection(
        connection: sqlite3.Connection, identity: str
    ) -> DataCleanupSelection:
        owners = PracticeOwnership(connection)
        # Measurement-attached analyses are removed with their owned run.
        runs = owners.resources(identity, "run")
        analyses: list[str] = []
        for record in owners.resources(identity, "analysis"):
            row = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT run_id FROM analysis_publications WHERE record_id=?",
                    (record,),
                ).fetchone(),
            )
            if row is None or row[0] not in runs:
                analyses.append(record)
        return DataCleanupSelection(
            runs=runs,
            analyses=tuple(analyses),
            procedures=owners.resources(identity, "procedure"),
            setups=owners.resources(identity, "setup"),
            setup_definitions=owners.resources(identity, "setup_definition"),
            parameters=owners.resources(identity, "parameters"),
            branches=owners.resources(identity, "branch"),
            plans=owners.resources(identity, "plan"),
            schedules=owners.resources(identity, "schedule"),
            calibration_tasks=owners.resources(identity, "calibration_task"),
            calibration_profiles=owners.resources(identity, "calibration_profile"),
        )

    def preview(self, identity: str) -> DataCleanupPreview:
        with self.store.sqlite.read_transaction() as connection:
            scope = self._scope(connection, identity)
            if scope.cleanup_preview is not None:
                return scope.cleanup_preview
            return self.cleanup.preview_in_transaction(
                connection,
                self._selection(connection, identity),
                practice_scope=identity,
            )

    def clear(
        self,
        identity: str,
        command: NotebookPracticeClearCommand,
        *,
        settle: Callable[[str], None],
    ) -> PracticeScope:
        with self._lock:
            with self.store.sqlite.write_transaction() as connection:
                scope = self._scope(connection, identity)
                if scope.cleanup_preview is not None:
                    if (
                        scope.cleanup_preview != command.preview
                        or scope.file_disposition != command.files
                    ):
                        raise ValueError(
                            "Resume cleanup with its original preview and file choice"
                        )
                else:
                    preview = self.cleanup.preview_in_transaction(
                        connection,
                        self._selection(connection, identity),
                        practice_scope=identity,
                    )
                    if preview != command.preview:
                        raise ValueError(
                            "Course content changed; preview cleanup again"
                        )
                    if preview.blockers:
                        raise ValueError(
                            "Resolve active tasks and retaining references "
                            "before cleanup"
                        )
                if scope.state == "cleared":
                    return scope
                scope = scope.model_copy(
                    update={
                        "state": "cleaning",
                        "cleanup_preview": command.preview,
                        "file_disposition": command.files,
                        "cleanup_error": None,
                    }
                )
                PracticeOwnership(connection).save(scope)
            try:
                operation = self.cleanup.execute(
                    DataCleanupCommand(
                        request_key=f"course:{identity}", preview=command.preview
                    ),
                    practice_scope=identity,
                    settle=settle,
                )
                if operation.state != "complete":
                    raise ValueError(
                        operation.error or "Course data cleanup is unfinished"
                    )
                scope = scope.model_copy(update={"state": "cleared"})
            except Exception as error:
                scope = scope.model_copy(update={"cleanup_error": str(error)})
                with self.store.sqlite.write_transaction() as connection:
                    PracticeOwnership(connection).save(scope)
                raise
            with self.store.sqlite.write_transaction() as connection:
                PracticeOwnership(connection).save(scope)
            return scope
