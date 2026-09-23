"""Persist fixed task intent and admit one dependency-ready stage atomically."""

import sqlite3
from datetime import UTC, datetime

from scopecat.daemon.calibration_checks import CalibrationTaskPreview
from scopecat.daemon.calibration_tasks import (
    CalibrationTaskControl,
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
            if task.mode in {"paused", "cancelled", "finished"}:
                raise BackendConflict(
                    f"task is {task.mode}; no new stages may be dispatched"
                )
            progress = self._view(connection, task).progress
            if command.stage_id not in progress.ready:
                raise BackendConflict("task stage prerequisites have not passed")
            updated = self._admit(connection, task, command.stage_id)
            self._store.update(connection, updated)
            return self._view(connection, updated)

    def control(self, command: CalibrationTaskControl) -> CalibrationTaskView:
        with self._sqlite.write_transaction() as connection:
            task = self._read(connection, command.task_id)
            if task.last_control == command:
                return self._view(connection, task)
            if task.control_revision != command.expected_revision:
                raise BackendConflict("task control revision changed")
            if task.mode in {"cancelled", "finished"}:
                raise BackendConflict(f"task is {task.mode}")
            mode = {"start": "running", "pause": "paused", "cancel": "cancelled"}[
                command.action
            ]
            updated = task.model_copy(
                update={
                    "mode": mode,
                    "control_revision": task.control_revision + 1,
                    "last_control": command,
                    "dispatch_errors": {}
                    if command.action == "start"
                    else task.dispatch_errors,
                }
            )
            self._store.update(connection, updated)
            return self._view(connection, updated)

    def running_ids(self, after: int = 0) -> list[tuple[int, str]]:
        with self._sqlite.read_transaction() as connection:
            return self._store.running_ids(connection, after)

    def advance(self, task_id: str) -> CalibrationTaskView:
        """Admit at most one stage; retain admission failures until explicit start."""
        with self._sqlite.write_transaction() as connection:
            task = self._read(connection, task_id)
            view = self._view(connection, task)
            if task.mode != "running":
                return view
            if view.progress.complete:
                task = task.model_copy(update={"mode": "finished"})
                self._store.update(connection, task)
                return self._view(connection, task)
            if any(
                stage.state
                in {"queued", "running", "waiting_for_input", "attention_required"}
                for stage in view.progress.stages
            ):
                return view
            for stage_id in view.progress.ready:
                if stage_id in task.dispatch_errors:
                    continue
                connection.execute("SAVEPOINT task_stage")
                try:
                    updated = self._admit(connection, task, stage_id)
                except (BackendConflict, BackendNotFound) as error:
                    connection.execute("ROLLBACK TO task_stage")
                    task = task.model_copy(
                        update={
                            "dispatch_errors": {
                                **task.dispatch_errors,
                                stage_id: str(error),
                            }
                        }
                    )
                else:
                    task = updated
                finally:
                    connection.execute("RELEASE task_stage")
                self._store.update(connection, task)
                if stage_id in task.executions:
                    break
            return self._view(connection, task)

    def _admit(
        self,
        connection: sqlite3.Connection,
        task: CalibrationTaskRecord,
        stage_id: str,
    ) -> CalibrationTaskRecord:
        call = task.specification.calls[stage_id]
        key = "calibration-task:" + sha256_json_hash(
            {"task": task.specification.task_id, "stage": stage_id}
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
        return task.model_copy(
            update={
                "executions": {**task.executions, stage_id: run.procedure_run_id},
                "dispatch_errors": {
                    key: value
                    for key, value in task.dispatch_errors.items()
                    if key != stage_id
                },
            }
        )

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
