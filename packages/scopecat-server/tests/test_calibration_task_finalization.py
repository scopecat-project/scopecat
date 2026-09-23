"""Finalization is a single durable handoff, never implied publication authority."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from scopecat.analysis.calibration import CHECK_RESULT
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ProcedureCloseCommand,
    ProcedureRunListQuery,
    ProcedureStepBeginCommand,
    ProcedureStepCompleteCommand,
    ProcedureWorkerLeaseAcquireCommand,
    RunOutputRef,
)
from scopecat.automation.calibration_tasks import (
    CalibrationTaskInputs,
    CalibrationTaskPlan,
    CalibrationTaskStage,
)
from scopecat.daemon.calibration_tasks import (
    CalibrationTaskCall,
    CalibrationTaskControl,
    CalibrationTaskCreate,
)
from scopecat.daemon.wire import AnalysisFactOutputPayload, AnalysisSaveCommand
from scopecat.project import load_project
from scopecat.records.analysis import AnalysisFact, RunAnalysisSubject
from scopecat.records.calibration_check import CalibrationCheckResult
from scopecat.sdk.compute import PYTHON_JSON_CODEC

from scopecat_server import BackendConflict, LocalDaemonRuntime
from scopecat_server.services.calibration_task_runner import CalibrationTaskRunner
from scopecat_server.services.project_workers import ProjectProcedureWorkers
from scopecat_server.snapshots import create_snapshot, restore_snapshot

from .test_calibration_check_admission import _check_case, _command
from .test_project_analysis_runtime import _complete_signal_run


@pytest.mark.parametrize("passed", [True, False])
def test_finalization_handoff_recovery_and_rejection(
    tmp_path: Path, passed: bool
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    with _check_case(root) as (runtime, check, child):
        app = runtime.application
        tasks = app.calibration_tasks
        command = _command(check)
        spec = CalibrationTaskCreate(
            task_id="round",
            plan=CalibrationTaskPlan(
                stages=(CalibrationTaskStage(id="fit", check=check),)
            ),
            calls={
                "fit": CalibrationTaskCall(
                    definition=command.definition,
                    intent=command.intent,
                    samples=command.samples,
                )
            },
            finalization=CalibrationTaskCall(
                definition=command.definition.model_copy(update={"id": "finalize"}),
                intent={"calibration_task": None, "captured_destination": "unchanged"},
                samples=command.samples,
            ),
        )
        created = tasks.create(spec)
        tasks.control(
            CalibrationTaskControl(
                task_id="round",
                expected_revision=created.task.control_revision,
                action="start",
                actor="test",
                reason="calibrate",
            )
        )
        admitted = tasks.advance("round")
        parent = app.automation.get(admitted.task.executions["fit"])
        assert tasks.advance("round").finalization is None
        lease = app.automation.acquire_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=parent.procedure_run_id,
                worker_id="test",
                expected_run_revision=parent.revision,
            )
        )
        run_id = _complete_signal_run(
            runtime, submission_id="measurement", signal=1.0, submission=child
        )
        saved = app.runs.save_run_analysis(
            run_id,
            AnalysisSaveCommand(
                title="Fit",
                analysis_key="fit",
                outputs=(
                    AnalysisFactOutputPayload(
                        kind="fact",
                        id=check.result_output,
                        title="Check",
                        content=AnalysisFact(
                            schema_id=CHECK_RESULT.id,
                            schema_codec=CHECK_RESULT.schema_codec,
                            schema_hash=CHECK_RESULT.schema_hash,
                            codec=PYTHON_JSON_CODEC,
                            value=CHECK_RESULT.encode(
                                CalibrationCheckResult(check.scope, passed)
                            ),
                        ),
                    ),
                ),
            ),
        )
        current = lease.run
        for step, output in (
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
                    step_key=step,
                    operation="run" if step == "measure" else "analysis",
                    intent_hash="sha256:" + "b" * 64,
                )
            )
            done = app.automation.complete_step(
                ProcedureStepCompleteCommand(
                    procedure_run_id=parent.procedure_run_id,
                    lease_token=lease.lease.lease_token,
                    expected_run_revision=begun.run.revision,
                    step_key=step,
                    attempt=begun.step.attempt,
                    expected_step_revision=begun.step.revision,
                    output=output,
                )
            )
            current = done.run
        app.automation.close(
            ProcedureCloseCommand(
                procedure_run_id=parent.procedure_run_id,
                lease_token=lease.lease.lease_token,
                expected_run_revision=current.revision,
                status="succeeded",
            )
        )
        if not passed:
            ended = tasks.advance("round")
            assert ended.task.mode == "finished" and ended.finalization is None
            assert not ended.progress.successful
            assert len(app.automation.list(ProcedureRunListQuery()).items) == 1
            return

        # Pause prevents the new side effect even after all checks pass.
        tasks.control(
            CalibrationTaskControl(
                task_id="round",
                expected_revision=admitted.task.control_revision,
                action="pause",
                actor="test",
                reason="inspect",
            )
        )
        assert tasks.advance("round").finalization is None
        tasks.control(
            CalibrationTaskControl(
                task_id="round",
                expected_revision=tasks.get("round").task.control_revision,
                action="start",
                actor="test",
                reason="continue",
            )
        )
        with (
            patch.object(
                tasks._store, "update", side_effect=RuntimeError("association lost")
            ),
            pytest.raises(RuntimeError, match="association lost"),
        ):
            tasks.advance("round")
        assert tasks.get("round").task.finalization_run_id is None
        assert len(app.automation.list(ProcedureRunListQuery()).items) == 1
        with patch.object(
            app.automation,
            "submit_in_transaction",
            side_effect=BackendConflict("temporary admission conflict"),
        ):
            failed = tasks.advance("round")
        assert failed.task.finalization_error == "temporary admission conflict"
        assert tasks.advance("round") == failed
        tasks.control(
            CalibrationTaskControl(
                task_id="round",
                expected_revision=failed.task.control_revision,
                action="start",
                actor="test",
                reason="retry admission",
            )
        )
        bound = tasks.advance("round")
        assert bound.finalization is not None and bound.task.mode == "running"
        assert tasks.advance("round") == bound
        inputs = CalibrationTaskInputs.model_validate(
            bound.finalization.intent["calibration_task"]
        )
        assert inputs.task_id == "round" and set(inputs.checks) == {"fit"}
        assert inputs.checks["fit"] == bound.progress.stages[0].evidence
        assert inputs.checks["fit"].analysis_record_id == saved.record.id
        assert bound.finalization.intent["captured_destination"] == "unchanged"
        assert bound.task.specification == spec
    with LocalDaemonRuntime(root) as restarted:
        assert restarted.application.calibration_tasks.advance("round") == bound
        workers = Mock(spec=ProjectProcedureWorkers)
        CalibrationTaskRunner(restarted.application.calibration_tasks, workers).tick()
        workers.manage.assert_called_once_with(bound.finalization.procedure_run_id)
    archive = tmp_path / "snapshot"
    restored = tmp_path / "restored"
    create_snapshot(load_project(root / "scopecat.toml"), archive)
    restore_snapshot(archive, restored)
    with LocalDaemonRuntime(restored) as recovered:
        app = recovered.application
        assert app.calibration_tasks.advance("round") == bound
        final = bound.finalization
        lease = app.automation.acquire_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=final.procedure_run_id,
                worker_id="test",
                expected_run_revision=final.revision,
            )
        )
        app.automation.close(
            ProcedureCloseCommand(
                procedure_run_id=final.procedure_run_id,
                lease_token=lease.lease.lease_token,
                expected_run_revision=lease.run.revision,
                status="failed",
                reason="laboratory policy rejected",
            )
        )
        ended = app.calibration_tasks.advance("round")
        assert ended.task.mode == "finished"
        assert ended.finalization is not None and ended.finalization.closure is not None
        assert ended.finalization.closure.status == "failed"
        assert (
            ended.progress.successful
        )  # Check progress is not final scientific success.
