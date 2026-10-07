"""Persist fixed task intent and admit one dependency-ready stage atomically."""

import sqlite3
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from scopecat.automation import ProcedureRun, ProcedureSubmitCommand
from scopecat.automation.calibration_tasks import (
    CalibrationTaskInputs,
    assess_calibration_task,
)
from scopecat.config.candidates import (
    CandidateConfig,
    resolve_candidate_config_snapshot,
)
from scopecat.config.changes import load_parameter_change_proposal
from scopecat.daemon.calibration_checks import CalibrationTaskPreview
from scopecat.daemon.calibration_tasks import (
    CalibrationStageAttempt,
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
from scopecat.records.calibration_check import CalibrationCheckRequest
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


@dataclass(frozen=True)
class PreparedTaskAdmissions:
    task: CalibrationTaskRecord
    commands: dict[str, ProcedureSubmitCommand | BackendConflict]


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
        self.validate_call: Callable[[ProcedureSubmitCommand], None] | None = None

    def create(self, specification: CalibrationTaskCreate) -> CalibrationTaskView:
        with self._sqlite.read_transaction() as connection:
            existing = self._store.read(connection, specification.task_id)
            if existing is not None:
                if existing.specification != specification:
                    raise BackendConflict(
                        "task ID already has a different specification"
                    )
                return self._view(connection, existing)
        calls = [
            *specification.calls.values(),
            *(r.call for r in specification.repairs.values()),
        ]
        if specification.finalization is not None:
            calls.append(specification.finalization)
        for call in calls:
            self._validate(
                ProcedureSubmitCommand(
                    request_key="calibration-task-template:" + specification.task_id,
                    definition=call.definition,
                    intent=call.intent,
                    samples=call.samples,
                    source=specification.source,
                )
            )
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
        prepared = self._prepare(command.task_id, command.stage_id)
        with self._sqlite.write_transaction() as connection:
            task = self._read(connection, command.task_id)
            if command.stage_id not in task.specification.calls:
                raise BackendConflict("task stage was not found")
            if command.stage_id in task.executions:
                return self._view(connection, task)
            self._require_prepared_task(task, prepared)
            if task.mode in {"paused", "cancelled", "finished"}:
                raise BackendConflict(
                    f"task is {task.mode}; no new stages may be dispatched"
                )
            progress = self._view(connection, task).progress
            if command.stage_id not in progress.ready:
                raise BackendConflict("task stage prerequisites have not passed")
            updated = self._admit(connection, task, command.stage_id, prepared)
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
                    "started_at": (task.started_at or datetime.now(UTC))
                    if command.action == "start"
                    else task.started_at,
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
        prepared = self._prepare(task_id)
        with self._sqlite.write_transaction() as connection:
            task = self._read(connection, task_id)
            self._require_prepared_task(task, prepared)
            view = self._view(connection, task)
            if task.mode != "running":
                return view
            if view.finalization is not None:
                if view.finalization.closure is None:
                    return view
                task = task.model_copy(update={"mode": "finished"})
                self._store.update(connection, task)
                return self._view(connection, task)
            if self._has_active_stage(view):
                return view
            reason = self._budget_reason(task)
            if reason is not None:
                return self._stop(connection, task, reason)
            if view.progress.complete:
                if (
                    task.specification.finalization is not None
                    and view.progress.successful
                ):
                    if task.finalization_error is not None:
                        return view
                    else:
                        connection.execute("SAVEPOINT task_finalization")
                        try:
                            task = self._admit_finalization(connection, view, prepared)
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
            for stage_id in view.progress.ready:
                if stage_id in task.dispatch_errors:
                    continue
                if self._phase(task, stage_id) == "repair":
                    reason = self._budget_reason(task, repairing=True)
                    if reason is not None:
                        return self._stop(connection, task, reason)
                connection.execute("SAVEPOINT task_stage")
                try:
                    updated = self._admit(connection, task, stage_id, prepared)
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
                if stage_id not in task.dispatch_errors:
                    break
            return self._view(connection, task)

    def _finalization_command(
        self, connection: sqlite3.Connection, view: CalibrationTaskView
    ) -> ProcedureSubmitCommand:
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
            repairs={
                key: evidence
                for key, attempts in task.attempts.items()
                for attempt in attempts
                if attempt.phase == "repair"
                if (
                    evidence := self._checks.evidence_in_transaction(
                        connection, attempt.procedure_run_id
                    )
                )
                is not None
            },
        )
        key = "calibration-task-finalization:" + sha256_json_hash(
            {"task": inputs.task_id}
        )
        return ProcedureSubmitCommand(
            definition=call.definition,
            intent={**call.intent, "calibration_task": inputs.model_dump(mode="json")},
            samples=call.samples,
            request_key=key,
            source=task.specification.source,
        )

    def _admit_finalization(
        self,
        connection: sqlite3.Connection,
        view: CalibrationTaskView,
        prepared: PreparedTaskAdmissions,
    ) -> CalibrationTaskRecord:
        command = self._finalization_command(connection, view)
        run = self._submit(connection, command, prepared)
        return view.task.model_copy(
            update={"finalization_run_id": run.procedure_run_id}
        )

    def _stage_command(
        self,
        connection: sqlite3.Connection,
        task: CalibrationTaskRecord,
        stage_id: str,
    ) -> tuple[
        ProcedureSubmitCommand,
        CalibrationCheckRequest,
        Literal["check", "repair", "verify"],
    ]:
        call = task.specification.calls[stage_id]
        stage = next(
            item for item in task.specification.plan.stages if item.id == stage_id
        )
        resolved = stage.check
        phase = self._phase(task, stage_id)
        reason = self._budget_reason(task, repairing=phase == "repair")
        if reason is not None:
            raise BackendConflict(reason)
        source_id: str | None = None
        proposal_id: str | None = None
        if phase == "repair":
            call = task.specification.repairs[stage_id].call
            resolved = CalibrationCheckRequest.model_validate(
                call.intent["calibration_check"]
            )
        elif phase == "verify":
            source_id = stage_id
            proposal_id = task.specification.repairs[stage_id].proposal_id
        elif stage.candidate_from is not None:
            source_id = stage.candidate_from.stage_id
            proposal_id = stage.candidate_from.proposal_id
        if source_id is not None:
            assert proposal_id is not None
            evidence = self._checks.evidence_in_transaction(
                connection, task.executions[source_id]
            )
            if (
                evidence is None
                or evidence.passed is not True
                or evidence.analysis_record_id is None
            ):
                raise BackendConflict("candidate source stage has no accepted analysis")
            try:
                proposal = load_parameter_change_proposal(
                    run_id=evidence.measurement.run_id,
                    selector=proposal_id,
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
                setup=self._services.runs.read_snapshot(
                    proposal.source_run_id
                ).execution_setup,
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
            {"task": task.specification.task_id, "stage": stage_id, "phase": phase}
        )
        return (
            ProcedureSubmitCommand(
                definition=call.definition,
                intent=call.intent,
                samples=call.samples,
                request_key=key,
                source=task.specification.source,
            ),
            resolved,
            phase,
        )

    def _admit(
        self,
        connection: sqlite3.Connection,
        task: CalibrationTaskRecord,
        stage_id: str,
        prepared: PreparedTaskAdmissions,
    ) -> CalibrationTaskRecord:
        command, resolved, phase = self._stage_command(connection, task, stage_id)
        run = self._submit(connection, command, prepared)
        return task.model_copy(
            update={
                "attempts": {
                    **task.attempts,
                    stage_id: (
                        *task.attempts.get(stage_id, ()),
                        CalibrationStageAttempt(
                            phase=phase,
                            procedure_run_id=run.procedure_run_id,
                            check=resolved,
                        ),
                    ),
                },
                "started_at": task.started_at or datetime.now(UTC),
                "dispatch_errors": {
                    key: value
                    for key, value in task.dispatch_errors.items()
                    if key != stage_id
                },
            }
        )

    @staticmethod
    def _has_active_stage(view: CalibrationTaskView) -> bool:
        return any(
            stage.state
            in {"queued", "running", "waiting_for_input", "attention_required"}
            for stage in view.progress.stages
        )

    @staticmethod
    def _require_prepared_task(
        task: CalibrationTaskRecord,
        prepared: PreparedTaskAdmissions,
    ) -> None:
        if task.specification.source is not None and task != prepared.task:
            raise BackendConflict("task changed during retained validation")

    def _validate(self, command: ProcedureSubmitCommand) -> None:
        if command.source is None:
            return
        if self.validate_call is None:
            raise BackendConflict("retained task validation is unavailable")
        self.validate_call(command)

    def _prepare(
        self,
        task_id: str,
        stage_id: str | None = None,
    ) -> PreparedTaskAdmissions:
        # Resolve evidence under a read snapshot, then release it before waiting
        # for authored validation. Admission reconstructs the exact command.
        commands: list[ProcedureSubmitCommand] = []
        with self._sqlite.read_transaction() as connection:
            task = self._read(connection, task_id)
            if task.specification.source is None:
                return PreparedTaskAdmissions(task, {})
            view = self._view(connection, task)
            if stage_id is None and (
                task.mode != "running"
                or view.finalization is not None
                or self._has_active_stage(view)
                or self._budget_reason(task) is not None
            ):
                return PreparedTaskAdmissions(task, {})
            if stage_id is not None:
                stages = (
                    [stage_id]
                    if stage_id in view.progress.ready
                    and stage_id not in task.executions
                    else []
                )
            elif task.mode == "running":
                stages = [
                    key
                    for key in view.progress.ready
                    if key not in task.dispatch_errors
                ]
                if (
                    view.progress.complete
                    and view.progress.successful
                    and task.specification.finalization is not None
                    and task.finalization_run_id is None
                    and task.finalization_error is None
                ):
                    # Rebuild under the write-side savepoint so the ordinary
                    # finalization error is retained until explicit start.
                    with suppress(BackendConflict, BackendNotFound):
                        commands.append(self._finalization_command(connection, view))
            else:
                stages = []
            for key in stages:
                try:
                    command, _, _ = self._stage_command(connection, task, key)
                except BackendConflict, BackendNotFound:
                    continue  # The write-side admission retains the ordinary error.
                commands.append(command)
        prepared: dict[str, ProcedureSubmitCommand | BackendConflict] = {}
        for command in commands:
            try:
                self._validate(command)
            except BackendConflict as error:
                prepared[command.request_key] = error
            else:
                prepared[command.request_key] = command
        return PreparedTaskAdmissions(task, prepared)

    def _submit(
        self,
        connection: sqlite3.Connection,
        command: ProcedureSubmitCommand,
        prepared: PreparedTaskAdmissions,
    ) -> ProcedureRun:
        if command.source is not None:
            validated = prepared.commands.get(command.request_key)
            if isinstance(validated, BackendConflict):
                raise validated
            if validated != command:
                raise BackendConflict(
                    "task or evidence changed during retained validation"
                )
        try:
            return self._automation.submit_in_transaction(
                connection,
                definition=command.definition,
                intent=command.intent,
                samples=command.samples,
                request_key=command.request_key,
                source=command.source,
                require_new=True,
            )
        except (AutomationConflict, AutomationNotFound) as error:
            raise BackendConflict(str(error)) from error

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
        preview = CalibrationTaskPreview(
            plan=task.resolved_plan, executions=task.executions
        )
        executions = self._checks.task_executions_in_transaction(connection, preview)
        progress = assess_calibration_task(preview.plan, executions)
        transitions: dict[
            str, Literal["repair_ready", "verification_ready", "failed"]
        ] = {}
        for item in progress.stages:
            attempts = task.attempts.get(item.id, ())
            if not attempts or item.id not in task.specification.repairs:
                continue
            closure = executions[item.id][0].closure
            if (
                closure is not None
                and closure.status == "failed"
                and item.state == "rejected"
            ):
                transitions[item.id] = "failed"
            if closure is None or closure.status != "succeeded":
                continue
            if attempts[-1].phase == "check" and item.state == "rejected":
                transitions[item.id] = "repair_ready"
            elif attempts[-1].phase == "repair" and item.state == "passed":
                transitions[item.id] = "verification_ready"
        return CalibrationTaskView(
            task=task,
            admission_deadline=task.started_at
            + task.specification.repair_budget.elapsed
            if task.started_at is not None
            and task.specification.repair_budget is not None
            else None,
            finalization=SQLiteAutomationStore(self._sqlite).read_run_in_transaction(
                connection, task.finalization_run_id
            )
            if task.finalization_run_id is not None
            else None,
            progress=assess_calibration_task(
                preview.plan, executions, transitions=transitions
            ),
        )

    @staticmethod
    def _phase(
        task: CalibrationTaskRecord, stage_id: str
    ) -> Literal["check", "repair", "verify"]:
        attempts = task.attempts.get(stage_id, ())
        if not attempts:
            return "check"
        return "repair" if attempts[-1].phase == "check" else "verify"

    @staticmethod
    def _budget_reason(
        task: CalibrationTaskRecord, *, repairing: bool = False
    ) -> Literal["deadline_elapsed", "repair_budget_exhausted"] | None:
        budget = task.specification.repair_budget
        if budget is None:
            return None
        if (
            task.started_at is not None
            and datetime.now(UTC) >= task.started_at + budget.elapsed
        ):
            return "deadline_elapsed"
        if repairing and task.repairs_used >= budget.max_repairs:
            return "repair_budget_exhausted"
        return None

    def _stop(
        self,
        connection: sqlite3.Connection,
        task: CalibrationTaskRecord,
        reason: Literal["deadline_elapsed", "repair_budget_exhausted"],
    ) -> CalibrationTaskView:
        task = task.model_copy(update={"mode": "finished", "stop_reason": reason})
        self._store.update(connection, task)
        return self._view(connection, task)
