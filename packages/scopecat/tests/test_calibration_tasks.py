"""Stage dependencies isolate failures without claiming scientific readiness."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from scopecat.automation import (
    ProcedureClosure,
    ProcedureDefinitionRef,
    ProcedureRun,
    procedure_intent_hash,
)
from scopecat.automation.calibration import CheckEvidence
from scopecat.automation.calibration_tasks import (
    CalibrationTaskPlan,
    CalibrationTaskStage,
    StageCandidateOutput,
    assess_calibration_task,
)
from scopecat.daemon.calibration_checks import CalibrationTaskPreview
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.calibration_check import CalibrationCheckRequest, CalibrationScope
from scopecat.records.measurement_context import MeasurementContext
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.run import RunSnapshot
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)

HASH = "sha256:" + "a" * 64


def _stage(id: str, *dependencies: str) -> CalibrationTaskStage:
    return CalibrationTaskStage(
        id=id,
        depends_on=dependencies,
        check=CalibrationCheckRequest(
            scope=CalibrationScope("drive", (id,), "idle", "1"),
            context=MeasurementContext(
                ParameterRevisionRef(revision_id="p", content_hash=HASH),
                UnboundSubject(),
                HASH,
                None,
            ),
            measurement_step="measure",
            analysis_step="assess",
        ),
    )


def _execution(
    stage: CalibrationTaskStage, passed: bool | None, *, failed: bool = False
) -> tuple[ProcedureRun, CheckEvidence | None]:
    definition = ProcedureDefinitionRef(id="check", version="1", fingerprint=HASH)
    at = datetime(2026, 9, 23, tzinfo=UTC)
    run = ProcedureRun(
        procedure_run_id=stage.id,
        request_key=stage.id,
        definition=definition,
        intent={},
        intent_hash=procedure_intent_hash(definition, {}),
        revision=3,
        state="closed",
        created_at=at,
        updated_at=at,
        closure=ProcedureClosure(
            closed_at=at,
            status="failed" if failed else "succeeded",
            reason="hardware" if failed else None,
        ),
    )
    measured = RunSnapshot(
        run_id=stage.id,
        config_content_hash=HASH,
        scientific_binding=ResolvedScientificBinding(
            subject=UnboundSubject(), config_content_hash=HASH, setup_content_hash=HASH
        ),
        outcome=RunOutcome(run_id=stage.id, result="succeeded", certainty="known"),
    )
    return run, None if passed is None else CheckEvidence(
        measured, stage.check.scope, "report", passed
    )


def test_candidate_source_requires_explicit_dependency() -> None:
    source = _stage("fit")
    consumer = _stage("verify").model_copy(
        update={
            "candidate_from": StageCandidateOutput(
                stage_id="fit", proposal_id="frequency"
            ),
        }
    )
    with pytest.raises(ValidationError, match="explicit prerequisite"):
        CalibrationTaskPlan(stages=(source, consumer))
    assert (
        CalibrationTaskPlan(
            stages=(
                source,
                consumer.model_copy(
                    update={
                        "depends_on": ("fit",),
                    }
                ),
            )
        )
        .stages[1]
        .candidate_from
        is not None
    )


def test_partial_task_blocks_dependants_but_preserves_independent_progress() -> None:
    a, b = _stage("a"), _stage("b")
    plan = CalibrationTaskPlan(
        stages=(_stage("joint", "a", "b"), _stage("other"), b, a)
    )
    initial = assess_calibration_task(plan, {})
    assert initial.ready == ("other", "b", "a")
    assert initial.stages[0].state == "waiting"
    rejected = assess_calibration_task(plan, {"a": _execution(a, False)})
    assert rejected.stages[0].state == "blocked"
    assert rejected.stages[0].blocked_by == ("a", "b")
    assert rejected.ready == ("other", "b")
    assert not rejected.complete and not rejected.successful
    complete = assess_calibration_task(
        plan,
        {
            "a": _execution(a, False),
            "b": _execution(b, True),
            "other": _execution(_stage("other"), True),
        },
    )
    assert complete.complete and not complete.successful
    assert complete.stages[0].blocked_by == ("a",)


def test_positive_result_unlocks_followup_without_skipping_required_stage() -> None:
    first, second = _stage("first"), _stage("second", "first")
    plan = CalibrationTaskPlan(stages=(first, second))
    executions = {"first": _execution(first, True)}
    assert assess_calibration_task(plan, executions).ready == ("second",)
    executions["second"] = _execution(second, True)
    result = assess_calibration_task(plan, executions)
    assert result.complete and result.successful and not result.ready


@pytest.mark.parametrize(
    "passed,failed,state",
    [
        (None, False, "incomplete"),
        (None, True, "failed"),
        (True, True, "failed"),
        (False, False, "rejected"),
    ],
)
def test_execution_success_alone_does_not_fulfill_stage(
    passed: bool | None, failed: bool, state: str
) -> None:
    stage = _stage("a")
    result = assess_calibration_task(
        CalibrationTaskPlan(stages=(stage,)),
        {"a": _execution(stage, passed, failed=failed)},
    )
    assert result.stages[0].state == state
    assert result.complete and not result.successful


def test_unfinished_acquisition_cannot_count_as_a_pass() -> None:
    stage = _stage("a")
    run, evidence = _execution(stage, True)
    assert evidence is not None
    evidence = replace(
        evidence, measurement=evidence.measurement.model_copy(update={"outcome": None})
    )
    result = assess_calibration_task(
        CalibrationTaskPlan(stages=(stage,)), {"a": (run, evidence)}
    )
    assert result.stages[0].state == "incomplete"


@pytest.mark.parametrize(
    "stages",
    [
        (_stage("a"), _stage("a")),
        (_stage("a", "missing"),),
        (_stage("a", "b"), _stage("b", "a")),
    ],
)
def test_invalid_dependency_graph_is_rejected(
    stages: tuple[CalibrationTaskStage, ...],
) -> None:
    with pytest.raises(ValidationError):
        CalibrationTaskPlan(stages=stages)


def test_one_execution_cannot_satisfy_two_stages() -> None:
    plan = CalibrationTaskPlan(stages=(_stage("a"), _stage("b")))
    with pytest.raises(ValidationError, match="multiple task stages"):
        CalibrationTaskPreview(plan=plan, executions={"a": "same", "b": "same"})
