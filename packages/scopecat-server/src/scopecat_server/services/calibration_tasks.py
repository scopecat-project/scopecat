"""Persist fixed task intent and admit one dependency-ready stage atomically."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

from scopecat.automation.calibration_tasks import CalibrationTaskInputs
from scopecat.config.candidates import (
    CandidateConfig,
    resolve_candidate_config_snapshot,
)
from scopecat.config.changes import load_parameter_change_proposal
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
from scopecat.kernel.errors import ProblemFailure
from scopecat.project_state import ProjectStateServices
from scopecat.records.candidate_input import AnalysisCandidateRunConfigSource
from scopecat.records.config import config_content_hash

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.services.automation import AutomationService
from scopecat_server.services.calibration_checks import CalibrationCheckQueries
from scopecat_server.storage.sqlite.automation import (
    AutomationConflict,
    AutomationNotFound,
    SQLiteAutomationStore,
)
from scopecat_server.storage.sqlite.calibration_tasks import CalibrationTaskStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase


class CalibrationTaskService:
    def __init__(
        self,
        sqlite: SQLiteDatabase,
        automation: AutomationService,
        checks: CalibrationCheckQueries,
        services: ProjectStateServices,
    ) -> None:
        self._sqlite = sqlite
        self._automation = automation
        self._checks = checks
        self._store = CalibrationTaskStore()
        self._services = services

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
                    "finalization_error": None
                    if command.action == "start"
                    else task.finalization_error,
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
                if (
                    task.specification.finalization is not None
                    and view.progress.successful
                ):
                    if view.finalization is not None:
                        if view.finalization.closure is None:
                            return view
                    elif task.finalization_error is not None:
                        return view
                    else:
                        connection.execute("SAVEPOINT task_finalization")
                        try:
                            task = self._admit_finalization(connection, view)
                        except (BackendConflict, BackendNotFound) as error:
                            connection.execute("ROLLBACK TO task_finalization")
                            task = task.model_copy(
                                update={"finalization_error": str(error)}
                            )
                        finally:
                            connection.execute("RELEASE task_finalization")
                        self._store.update(connection, task)
                        return self._view(connection, task)
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

    def _admit_finalization(
        self, connection: sqlite3.Connection, view: CalibrationTaskView
    ) -> CalibrationTaskRecord:
        task = view.task
        call = task.specification.finalization
        assert call is not None
        inputs = CalibrationTaskInputs(
            task_id=task.specification.task_id,
            checks={
                stage.id: stage.evidence
                for stage in view.progress.stages
                if stage.evidence is not None
            },
        )
        key = "calibration-task-finalization:" + sha256_json_hash(
            {"task": inputs.task_id}
        )
        try:
            run = self._automation.submit_in_transaction(
                connection,
                definition=call.definition,
                intent={
                    **call.intent,
                    "calibration_task": inputs.model_dump(mode="json"),
                },
                samples=call.samples,
                request_key=key,
                require_new=True,
            )
        except (AutomationConflict, AutomationNotFound) as error:
            raise BackendConflict(str(error)) from error
        return task.model_copy(update={"finalization_run_id": run.procedure_run_id})

    def _admit(
        self,
        connection: sqlite3.Connection,
        task: CalibrationTaskRecord,
        stage_id: str,
    ) -> CalibrationTaskRecord:
        call = task.specification.calls[stage_id]
        stage = next(
            item for item in task.specification.plan.stages if item.id == stage_id
        )
        resolved = stage.check
        if stage.candidate_from is not None:
            source_stage = stage.candidate_from
            progress = self._view(connection, task).progress
            source = next(
                item for item in progress.stages if item.id == source_stage.stage_id
            )
            evidence = source.evidence
            if (
                source.state != "passed"
                or evidence is None
                or evidence.analysis_record_id is None
            ):
                raise BackendConflict("candidate source stage has no accepted analysis")
            try:
                proposal = load_parameter_change_proposal(
                    run_id=evidence.measurement.run_id,
                    selector=source_stage.proposal_id,
                    services=self._services,
                )
                if proposal.analysis_record_id != evidence.analysis_record_id:
                    raise BackendConflict(
                        "candidate must belong to the source stage's adopted analysis"
                    )
                config = resolve_candidate_config_snapshot(
                    CandidateConfig(proposal), services=self._services
                )
            except ProblemFailure as error:
                raise BackendConflict(
                    "stage candidate output could not be resolved"
                ) from error
            binding = evidence.measurement.scientific_binding
            expected = stage.check.context
            if (
                binding.subject != expected.subject
                or binding.target_binding != expected.target_binding
                or binding.setup_content_hash != expected.setup_content_hash
                or binding.scenario != expected.scenario
            ):
                raise BackendConflict(
                    "stage candidate changes the requested scientific context"
                )
            candidate = AnalysisCandidateRunConfigSource(
                source_run_id=proposal.source_run_id,
                proposal_id=proposal.id,
                analysis_record_id=proposal.analysis_record_id,
                base_config_content_hash=proposal.base_config_content_hash,
                content_hash=config_content_hash(config),
            )
            resolved = stage.check.model_copy(
                update={"context": replace(expected, parameters=candidate)}
            )
            call = call.model_copy(
                update={
                    "intent": {
                        **call.intent,
                        "calibration_check": resolved.model_dump(mode="json"),
                    }
                }
            )
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
                "resolved_checks": {**task.resolved_checks, stage_id: resolved},
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
            finalization=SQLiteAutomationStore(self._sqlite).read_run_in_transaction(
                connection, task.finalization_run_id
            )
            if task.finalization_run_id is not None
            else None,
            progress=self._checks.preview_in_transaction(
                connection,
                CalibrationTaskPreview(
                    plan=task.resolved_plan,
                    executions=task.executions,
                ),
            ),
        )
