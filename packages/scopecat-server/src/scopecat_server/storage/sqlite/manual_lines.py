"""Finite line catalog and queries over existing procedure/run authority."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, cast

from scopecat.automation.models import ProcedureRun
from scopecat.automation.wire import procedure_step_operation_id
from scopecat.records.experiment_plan import ExperimentPlanRevision, ExperimentPlanSave
from scopecat.records.manual_line import ManualLineSelection
from scopecat.records.manual_line_scan import (
    ManualLineLabels,
    ManualLineScan,
    ManualLineScanCreate,
)
from scopecat.records.plan_ref import ExperimentPlanRef

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.experiment_plan_repository import (
    ExperimentPlanRepository,
)
from scopecat_server.storage.sqlite.practice import PracticeOwnership
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository

if TYPE_CHECKING:
    from scopecat.automation.models import (
        ProcedureDefinitionRef,
        ProcedureIntent,
        ProcedureSource,
    )
    from scopecat.control.models import ControlRunState
    from scopecat.kernel.run_outcome import RunOutcome
    from scopecat.records.scientific_binding import ResolvedScientificBinding

    from .project_store import SQLiteProjectStore


def unresolved_line_result(state: ControlRunState, outcome: RunOutcome | None) -> bool:
    if state != "closed" or outcome is None:
        return True
    if outcome.certainty == "known":
        return False
    # Existing explicit attention-close evidence, not a new reconciliation path.
    return not any(
        item.code == "daemon.executor_loss_reconciled" for item in outcome.problems
    )


class ManualLineStore:
    def __init__(self, store: SQLiteProjectStore) -> None:
        self.store = store

    def plan_in_transaction(
        self, connection: sqlite3.Connection, scan_id: str
    ) -> ExperimentPlanRevision:
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                "SELECT r.revision,r.content_hash FROM experiment_plan_revisions r "
                "JOIN experiment_plan_heads h ON h.plan_id=r.plan_id "
                "AND h.revision=r.revision WHERE r.plan_id=?",
                (scan_id,),
            ).fetchone(),
        )
        if row is None:
            raise BackendNotFound("Physical line scan was not found")
        PracticeOwnership(connection).require_writer("plan", scan_id)
        return ExperimentPlanRepository(self.store).get_in_transaction(
            connection,
            ExperimentPlanRef(
                plan_id=scan_id,
                revision=cast("int", row["revision"]),
                content_hash=cast("str", row["content_hash"]),
            ),
        )

    @staticmethod
    def from_plan(plan: ExperimentPlanRevision) -> ManualLineScan:
        if plan.definition.manual_lines is None:
            raise BackendNotFound("Plan is not a physical line list")
        return ManualLineScan(
            scan_id=plan.ref.plan_id,
            name=plan.name,
            saved_by=plan.saved_by,
            lines=plan.definition.manual_lines,
            template=plan.definition.model_copy(update={"manual_lines": None}),
            revision=plan.ref.revision,
            saved_at=plan.saved_at,
        )

    def get_in_transaction(
        self, connection: sqlite3.Connection, scan_id: str
    ) -> ManualLineScan:
        return self.from_plan(self.plan_in_transaction(connection, scan_id))

    def get(self, scan_id: str) -> ManualLineScan:
        with self.store.sqlite.read_connection() as connection:
            return self.get_in_transaction(connection, scan_id)

    def list(self) -> tuple[ManualLineScan, ...]:
        return tuple(
            self.from_plan(plan)
            for plan in ExperimentPlanRepository(self.store).list().items
            if plan.definition.manual_lines is not None
        )

    def create(self, command: ManualLineScanCreate) -> ManualLineScan:
        if command.template.manual_lines is not None:
            raise BackendConflict(
                "Choose a measurement template without another line list"
            )
        plan = ExperimentPlanRepository(self.store).save(
            ExperimentPlanSave(
                name=command.name,
                saved_by=command.saved_by,
                definition=command.template.model_copy(
                    update={"manual_lines": command.lines}
                ),
            ),
            creation_id=command.scan_id,
        )
        return self.from_plan(plan)

    def labels(self, scan_id: str, command: ManualLineLabels) -> ManualLineScan:
        with self.store.sqlite.read_connection() as connection:
            current = self.plan_in_transaction(connection, scan_id)
        if current.ref.revision != command.expected_revision:
            raise BackendConflict("Line names changed; reload before editing")
        if tuple(
            line.line_id for line in current.definition.manual_lines or ()
        ) != tuple(line.line_id for line in command.lines):
            raise BackendConflict("Renaming preserves the finite ordered line IDs")
        saved = ExperimentPlanRepository(self.store).save(
            ExperimentPlanSave(
                name=command.name,
                saved_by=command.saved_by,
                previous=current.ref,
                definition=current.definition.model_copy(
                    update={"manual_lines": command.lines}
                ),
            )
        )
        return self.from_plan(saved)

    @staticmethod
    def attempts_in_transaction(
        connection: sqlite3.Connection, scan_id: str
    ) -> tuple[ProcedureRun, ...]:
        rows = cast(
            "list[sqlite3.Row]",
            connection.execute(
                "SELECT run_json FROM procedure_runs WHERE "
                "json_extract(run_json, '$.intent.manual_line.scan_id')=? "
                "AND definition_id LIKE 'scopecat.author:%' ORDER BY sequence",
                (scan_id,),
            ).fetchall(),
        )
        return tuple(
            ProcedureRun.model_validate_json(cast("str", row["run_json"]))
            for row in rows
        )

    def require_admission(
        self,
        connection: sqlite3.Connection,
        *,
        definition: ProcedureDefinitionRef,
        intent: ProcedureIntent,
        source: ProcedureSource | None,
        binding: ResolvedScientificBinding | None,
    ) -> None:
        if (
            not definition.id.startswith("scopecat.author:")
            or intent.get("manual_line") is None
        ):
            return
        selected = ManualLineSelection.model_validate(intent["manual_line"])
        scan = self.get_in_transaction(connection, selected.scan_id)
        template = scan.template
        if (
            definition.id != f"scopecat.author:{template.experiment}"
            or definition.fingerprint != template.version
            or source is None
            or source.workspace_id != template.workspace_id
            or source.code_revision != template.code_revision
        ):
            raise BackendConflict(
                "Line measurement requires its retained template source"
            )
        if binding is None or binding.model_dump(
            exclude={"config_content_hash"}
        ) != template.scientific_binding.model_dump(exclude={"config_content_hash"}):
            raise BackendConflict(
                "Changed subject, target or setup requires a separate line scan"
            )
        line = next(
            (item for item in scan.lines if item.line_id == selected.line_id), None
        )
        if line is None or selected.label != line.label:
            raise BackendConflict(
                "Line identity or label changed; reload the line list"
            )
        prior = self.attempts_in_transaction(connection, selected.scan_id)
        control = SQLiteControlPlane(self.store.sqlite)
        runs = SQLiteRunRepository(self.store.sqlite, self.store.objects.root)
        for attempt in prior:
            if attempt.state != "closed":
                raise BackendConflict(
                    "Finish or reconcile the current line attempt first"
                )
            child = control.find_run_by_submission_id_in_transaction(
                connection,
                procedure_step_operation_id(attempt.procedure_run_id, "experiment"),
            )
            if child is not None:
                snapshot = runs.read_snapshot_in_transaction(
                    connection, child.admission.run_id
                )
                if unresolved_line_result(child.state, snapshot.outcome):
                    raise BackendConflict(
                        "Resolve the existing line's uncertain result before continuing"
                    )
        attempted = {
            ManualLineSelection.model_validate(item.intent["manual_line"]).line_id
            for item in prior
        }
        first_new = next(
            (item.line_id for item in scan.lines if item.line_id not in attempted), None
        )
        if selected.line_id not in attempted and selected.line_id != first_new:
            raise BackendConflict("Start unmeasured lines in their saved order")
