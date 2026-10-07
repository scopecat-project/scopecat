"""Repair admissions are bounded, durable and separate from scientific success."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from scopecat.analysis.calibration import CHECK_RESULT
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ProcedureCloseCommand,
    ProcedureSource,
    ProcedureStepBeginCommand,
    ProcedureStepCompleteCommand,
    ProcedureSubmitCommand,
    ProcedureWorkerLeaseAcquireCommand,
    RunOutputRef,
)
from scopecat.automation.calibration_tasks import (
    CalibrationTaskPlan,
    CalibrationTaskStage,
)
from scopecat.daemon.calibration_tasks import (
    CalibrationRepairBudget,
    CalibrationStageRepair,
    CalibrationTaskCall,
    CalibrationTaskControl,
    CalibrationTaskCreate,
)
from scopecat.daemon.wire import (
    AnalysisFactOutputPayload,
    AnalysisSaveCommand,
    RunSubmission,
)
from scopecat.records.analysis import AnalysisFact, RunAnalysisSubject
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationCheckResult,
)
from scopecat.sdk.compute import PYTHON_JSON_CODEC

from scopecat_server import LocalDaemonRuntime
from scopecat_server.services import calibration_tasks as tasks_module

from .test_calibration_check_admission import _check_case, _command
from .test_project_analysis_runtime import _complete_signal_run


def specification(
    check: CalibrationCheckRequest, *, budget: int = 1
) -> CalibrationTaskCreate:
    original = _command(check)
    repair = _command(
        check.model_copy(update={"scope": replace(check.scope, capability="fit")})
    )
    return CalibrationTaskCreate(
        task_id="repair",
        plan=CalibrationTaskPlan(stages=(CalibrationTaskStage(id="q0", check=check),)),
        calls={
            "q0": CalibrationTaskCall(
                definition=original.definition,
                intent=original.intent,
                samples=original.samples,
            )
        },
        repairs={
            "q0": CalibrationStageRepair(
                call=CalibrationTaskCall(
                    definition=repair.definition,
                    intent=repair.intent,
                    samples=repair.samples,
                ),
                proposal_id="candidate",
            )
        },
        repair_budget=CalibrationRepairBudget(
            max_repairs=budget, elapsed=timedelta(minutes=10)
        ),
    )


def finish_check(
    runtime: LocalDaemonRuntime,
    child: RunSubmission,
    *,
    passed: bool,
    failed: bool = False,
) -> None:
    app = runtime.application
    task = app.calibration_tasks.get("repair").task
    attempt = task.attempts["q0"][-1]
    parent = app.automation.get(attempt.procedure_run_id)
    lease = app.automation.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=parent.procedure_run_id,
            worker_id="test",
            expected_run_revision=parent.revision,
        )
    )
    run_id = _complete_signal_run(
        runtime,
        submission_id=parent.procedure_run_id,
        signal=1.0,
        submission=child.model_copy(update={"submission_id": parent.procedure_run_id}),
    )
    saved = app.runs.save_run_analysis(
        run_id,
        AnalysisSaveCommand(
            title="Check",
            analysis_key="check",
            outputs=(
                AnalysisFactOutputPayload(
                    kind="fact",
                    id=attempt.check.result_output,
                    title="Check",
                    content=AnalysisFact(
                        schema_id=CHECK_RESULT.id,
                        schema_codec=CHECK_RESULT.schema_codec,
                        schema_hash=CHECK_RESULT.schema_hash,
                        codec=PYTHON_JSON_CODEC,
                        value=CHECK_RESULT.encode(
                            CalibrationCheckResult(attempt.check.scope, passed)
                        ),
                    ),
                ),
            ),
        ),
    )
    current = lease.run
    for key, output in (
        ("measure", RunOutputRef(run_id=run_id)),
        (
            "assess",
            AnalysisPublicationOutputRef(
                subject=RunAnalysisSubject(run_id=run_id),
                analysis_record_id=saved.record.id,
            ),
        ),
    ):
        begun = app.automation.begin_step(
            ProcedureStepBeginCommand(
                procedure_run_id=parent.procedure_run_id,
                lease_token=lease.lease.lease_token,
                expected_run_revision=current.revision,
                step_key=key,
                operation="run" if key == "measure" else "analysis",
                intent_hash="sha256:" + "b" * 64,
            )
        )
        current = app.automation.complete_step(
            ProcedureStepCompleteCommand(
                procedure_run_id=parent.procedure_run_id,
                lease_token=lease.lease.lease_token,
                expected_run_revision=begun.run.revision,
                step_key=key,
                attempt=begun.step.attempt,
                expected_step_revision=begun.step.revision,
                output=output,
            )
        ).run
    app.automation.close(
        ProcedureCloseCommand(
            procedure_run_id=parent.procedure_run_id,
            lease_token=lease.lease.lease_token,
            expected_run_revision=current.revision,
            status="failed" if failed else "succeeded",
            reason="execution failed after analysis" if failed else None,
        )
    )


def start(runtime: LocalDaemonRuntime) -> None:
    tasks = runtime.application.calibration_tasks
    view = tasks.get("repair")
    tasks.control(
        CalibrationTaskControl(
            task_id="repair",
            expected_revision=view.task.control_revision,
            action="start",
            actor="test",
            reason="continue",
        )
    )


def test_execution_failure_after_negative_fact_does_not_trigger_repair(
    tmp_path: Path,
) -> None:
    with _check_case(tmp_path) as (runtime, check, child):
        tasks = runtime.application.calibration_tasks
        tasks.create(specification(check))
        start(runtime)
        tasks.advance("repair")
        finish_check(runtime, child, passed=False, failed=True)
        ended = tasks.advance("repair")
        assert ended.task.mode == "finished"
        assert ended.progress.stages[0].state == "failed"
        assert ended.task.repairs_used == 0


def test_budget_stop_preserves_negative_check_after_restart(tmp_path: Path) -> None:
    with _check_case(tmp_path) as (runtime, check, child):
        tasks = runtime.application.calibration_tasks
        tasks.create(specification(check, budget=0))
        start(runtime)
        tasks.advance("repair")
        finish_check(runtime, child, passed=False)
        assert tasks.get("repair").progress.stages[0].state == "repair_ready"
        stopped = tasks.advance("repair")
        assert stopped.task.stop_reason == "repair_budget_exhausted"
        assert stopped.task.repairs_used == 0 and stopped.finalization is None
        assert len(stopped.task.attempts["q0"]) == 1
    with LocalDaemonRuntime(tmp_path) as reopened:
        assert reopened.application.calibration_tasks.get("repair") == stopped


def test_deadline_waits_for_admitted_work_and_does_not_reset_on_resume(
    tmp_path: Path,
) -> None:
    with _check_case(tmp_path) as (runtime, check, child):
        tasks = runtime.application.calibration_tasks
        tasks.create(specification(check))
        start(runtime)
        admitted = tasks.advance("repair")
        start(runtime)
        assert tasks.get("repair").task.started_at == admitted.task.started_at
        assert admitted.admission_deadline is not None
        with patch.object(tasks_module, "datetime") as clock:
            clock.now.return_value = admitted.admission_deadline + timedelta(seconds=1)
            assert tasks.advance("repair").task.mode == "running"
            finish_check(runtime, child, passed=False)
            stopped = tasks.advance("repair")
            assert stopped.task.stop_reason == "deadline_elapsed"
            assert len(stopped.task.attempts["q0"]) == 1


@pytest.mark.parametrize("retained", [False, True])
def test_verification_admission_failure_never_repeats_repair(
    tmp_path: Path, retained: bool
) -> None:
    with _check_case(tmp_path) as (runtime, check, child):
        tasks = runtime.application.calibration_tasks
        source = (
            ProcedureSource(
                workspace_id="workspace",
                code_revision=AuthorRevisionRef(content_hash="sha256:" + "a" * 64),
            )
            if retained
            else None
        )
        validated: list[ProcedureSubmitCommand] = []
        tasks.validate_call = validated.append
        tasks.create(specification(check).model_copy(update={"source": source}))
        start(runtime)
        tasks.advance("repair")
        finish_check(runtime, child, passed=False)
        repair = tasks.advance("repair")
        assert repair.task.attempts["q0"][-1].phase == "repair"
        run = runtime.application.automation.get(repair.task.executions["q0"])
        assert run.source == source
        if retained:
            assert validated[-1].intent == run.intent
            assert len(validated) == 4  # Both templates, initial check and repair.
        finish_check(runtime, child, passed=True)  # Deliberately no candidate output.
        blocked = tasks.advance("repair")
        assert "q0" in blocked.task.dispatch_errors
        assert blocked.task.repairs_used == 1
        start(runtime)
        retried = tasks.advance("repair")
        assert retried.task.attempts == blocked.task.attempts
        assert retried.finalization is None
