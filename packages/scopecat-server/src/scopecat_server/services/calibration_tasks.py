"""Persist fixed task intent and admit one dependency-ready stage atomically."""

import sqlite3
from datetime import UTC, datetime

from scopecat.daemon.calibration_checks import CalibrationTaskPreview
from scopecat.daemon.calibration_tasks import (
    CalibrationTaskCreate,
    CalibrationTaskDispatch,
    CalibrationTaskListQuery,
    CalibrationTaskPage,
    CalibrationTaskRecord,
    CalibrationTaskView,
)
from scopecat.kernel.content_identity import sha256_json_hash

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.services.automation import AutomationService
from scopecat_server.services.calibration_checks import CalibrationCheckQueries
from scopecat_server.storage.sqlite.automation import (
    AutomationConflict,
    AutomationNotFound,
)
from scopecat_server.storage.sqlite.calibration_tasks import CalibrationTaskStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase


class CalibrationTaskService:
    def __init__(
        self,
        sqlite: SQLiteDatabase,
        automation: AutomationService,
        checks: CalibrationCheckQueries,
    ) -> None:
        self._sqlite = sqlite
        self._automation = automation
        self._checks = checks
        self._store = CalibrationTaskStore()

    def create(self, specification: CalibrationTaskCreate) -> CalibrationTaskView:
        with self._sqlite.write_transaction() as connection:
            task = self._store.read(connection, specification.task_id)
            if task is not None:
                if task.specification != specification:
                    raise BackendConflict(
                        "task ID already has a different specification"
                    )
            else:
                task = CalibrationTaskRecord(
                    specification=specification, created_at=datetime.now(UTC)
                )
                self._store.insert(connection, task)
            return self._view(connection, task)

    def get(self, task_id: str) -> CalibrationTaskView:
        with self._sqlite.read_transaction() as connection:
            return self._view(connection, self._read(connection, task_id))

    def list(self, query: CalibrationTaskListQuery) -> CalibrationTaskPage:
        with self._sqlite.read_transaction() as connection:
            return self._store.list(connection, query)

    def dispatch(self, command: CalibrationTaskDispatch) -> CalibrationTaskView:
        with self._sqlite.write_transaction() as connection:
            task = self._read(connection, command.task_id)
            if command.stage_id not in task.specification.calls:
                raise BackendConflict("task stage was not found")
            if command.stage_id in task.executions:
                return self._view(connection, task)
            progress = self._view(connection, task).progress
            if command.stage_id not in progress.ready:
                raise BackendConflict("task stage prerequisites have not passed")
            call = task.specification.calls[command.stage_id]
            key = "calibration-task:" + sha256_json_hash(
                {"task": command.task_id, "stage": command.stage_id}
            )
            try:
                run = self._automation.submit_in_transaction(
                    connection,
                    definition=call.definition,
                    intent=call.intent,
                    samples=call.samples,
                    request_key=key,
                    require_new=True,
                )
            except (AutomationConflict, AutomationNotFound) as error:
                raise BackendConflict(str(error)) from error
            updated = task.model_copy(
                update={
                    "executions": {
                        **task.executions,
                        command.stage_id: run.procedure_run_id,
                    }
                }
            )
            self._store.update(connection, updated)
            return self._view(connection, updated)

    def _read(
        self, connection: sqlite3.Connection, task_id: str
    ) -> CalibrationTaskRecord:
        task = self._store.read(connection, task_id)
        if task is None:
            raise BackendNotFound("calibration task was not found")
        return task

    def _view(
        self, connection: sqlite3.Connection, task: CalibrationTaskRecord
    ) -> CalibrationTaskView:
        return CalibrationTaskView(
            task=task,
            progress=self._checks.preview_in_transaction(
                connection,
                CalibrationTaskPreview(
                    plan=task.specification.plan, executions=task.executions
                ),
            ),
        )
