"""Transactional child-run checks for suspended procedure workers."""

import sqlite3
from datetime import UTC, datetime

from scopecat.automation.wire import procedure_step_operation_id
from scopecat.kernel.problems import ProblemPhase, problem
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.runs.repository import TerminalRunCommit

from scopecat_server.storage.sqlite.automation import AutomationConflict
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository

from .point_plans import RunPointPlanService


class ProcedureResourceWaits:
    def __init__(
        self,
        control: SQLiteControlPlane,
        runs: SQLiteRunRepository,
        point_plans: RunPointPlanService,
    ) -> None:
        self.control = control
        self.runs = runs
        self.point_plans = point_plans

    def validate(
        self,
        connection: sqlite3.Connection,
        procedure_id: str,
        step_key: str,
        run_id: str,
    ) -> None:
        child = self.control.get_run_in_transaction(connection, run_id)
        if child.admission.submission_id != procedure_step_operation_id(
            procedure_id, step_key
        ):
            raise AutomationConflict("resource wait child does not belong to this step")
        if child.state != "queued" or self._has_segment(connection, run_id):
            raise AutomationConflict(
                "resource wait requires a child that never started"
            )

    def ready(self, connection: sqlite3.Connection, run_id: str) -> bool:
        child = self.control.get_run_in_transaction(connection, run_id)
        if child.state == "closed":
            return True  # Replay the retained terminal outcome, never reacquire.
        if child.state != "queued" or self._has_segment(connection, run_id):
            return False
        return (
            connection.execute(
                """SELECT 1 FROM run_resource_claims AS needed
               JOIN resource_claims AS held USING (resource_kind, resource_id)
               WHERE needed.run_id = ? LIMIT 1""",
                (run_id,),
            ).fetchone()
            is None
        )

    def cancel(self, connection: sqlite3.Connection, run_id: str) -> bool:
        child = self.control.get_run_in_transaction(connection, run_id)
        if child.state == "closed":
            return True
        if child.state != "queued" or self._has_segment(connection, run_id):
            return False
        now = datetime.now(tz=UTC)
        outcome = RunOutcome(
            run_id=run_id,
            result="cancelled",
            certainty="known",
            finished_at=now,
            problems=(
                problem(
                    "procedure_cancelled_before_execution",
                    "parent procedure cancelled while waiting for resources",
                    phase=ProblemPhase.EXECUTION,
                ),
            ),
        )
        prepared = self.runs.prepare_terminal_commit(
            TerminalRunCommit(run_id=run_id, outcome=outcome)
        )
        self.control.request_run_cancellation_in_transaction(connection, run_id, at=now)
        self.point_plans.abandon_in_transaction(
            connection,
            run_id,
            operation_id="point-plan.terminal.procedure-cancelled",
            reason="parent procedure cancelled",
        )
        self.runs.commit_prepared_terminal_in_transaction(connection, prepared)
        self.control.close_run_in_transaction(connection, run_id, at=now)
        return True

    @staticmethod
    def _has_segment(connection: sqlite3.Connection, run_id: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM run_execution_segments WHERE run_id = ? LIMIT 1",
                (run_id,),
            ).fetchone()
            is not None
        )
