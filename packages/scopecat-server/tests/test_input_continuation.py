"""Exact input handoff permissions survive retries without releasing other pauses."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from scopecat.analysis.facts import analysis_fact_structure_hash
from scopecat.automation import (
    InterpretationOutputRef,
    InterpretationRequest,
    InterpretationResponse,
    ProcedureDefinitionRef,
    ProcedureRun,
    ProcedureStepAttempt,
    procedure_intent_hash,
)
from scopecat.automation.wire import (
    ProcedureStepInputSubmitCommand,
    ProcedureStepInputSubmitReceipt,
    ProcedureStepInputWaitReceipt,
)

from scopecat_server.services.project_workers import ProjectProcedureWorkers


def question():
    now = datetime.now(UTC)
    request = InterpretationRequest(
        title="Confirm",
        instructions="Review the evidence",
        schema_id="confirm.v1",
        schema_hash=analysis_fact_structure_hash({}),
        structure={},
    )
    definition = ProcedureDefinitionRef(
        id="confirm", version="1", fingerprint="sha256:" + "2" * 64
    )
    run = ProcedureRun(
        procedure_run_id="p1",
        request_key="one",
        definition=definition,
        intent={},
        intent_hash=procedure_intent_hash(definition, {}),
        revision=4,
        state="waiting_for_input",
        created_at=now,
        updated_at=now,
    )
    step = ProcedureStepAttempt(
        procedure_run_id="p1",
        step_key="choose/1",
        attempt=1,
        operation="interpretation",
        intent_hash=request.request_hash,
        revision=2,
        state="waiting_for_input",
        started_at=now,
        updated_at=now,
        interpretation_request=request,
    )
    command = ProcedureStepInputSubmitCommand(
        procedure_run_id="p1",
        step_key=step.step_key,
        attempt=1,
        expected_run_revision=4,
        expected_step_revision=2,
        request_hash=request.request_hash,
        actor="learner",
        actor_kind="human",
        value={},
    )
    output = InterpretationOutputRef(
        procedure_run_id="p1",
        step_key=step.step_key,
        request_hash=request.request_hash,
        response=InterpretationResponse(
            actor="learner", actor_kind="human", value={}, submitted_at=now
        ),
    )
    return (
        ProcedureStepInputWaitReceipt(run=run, step=step),
        command,
        ProcedureStepInputSubmitReceipt(
            run=run.model_copy(update={"state": "ready", "revision": 5}),
            step=step.model_copy(
                update={
                    "state": "succeeded",
                    "revision": 3,
                    "output": output,
                    "finished_at": now,
                }
            ),
            output=output,
        ),
    )


def test_answer_before_worker_exit_queues_once_and_failure_does_not_resume(
    tmp_path: Path,
):
    wait, command, answer = question()
    state = ["waiting_for_input"]
    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: state[0])
    manager.manage("p1")
    child = Mock()
    child.poll.return_value = None
    manager._children["p1"] = child
    manager.wait_for_input(lambda: wait)
    state[0] = "ready"
    with patch.object(manager, "_spawn") as spawn:
        assert (
            manager.submit_input_and_continue(command, lambda: answer).continuation
            == "queued"
        )
        assert (
            manager.submit_input_and_continue(command, lambda: answer).continuation
            == "queued"
        )
        spawn.assert_not_called()
        child.poll.return_value = 0
        manager.tick()
        spawn.assert_called_once_with("p1")
        assert (
            manager.submit_input_and_continue(command, lambda: answer).continuation
            == "already_continued"
        )
        spawn.assert_called_once()


@pytest.mark.parametrize("code", [1, -9])
def test_failed_handoff_worker_cannot_be_released_by_answer(tmp_path: Path, code: int):
    wait, command, answer = question()
    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
    manager.manage("p1")
    manager.wait_for_input(lambda: wait)
    child = Mock()
    child.poll.return_value = code
    manager._children["p1"] = child
    with patch.object(manager, "_spawn") as spawn:
        assert (
            manager.submit_input_and_continue(command, lambda: answer).continuation
            == "blocked"
        )
        assert (
            manager.submit_input_and_continue(command, lambda: answer).continuation
            == "blocked"
        )
        spawn.assert_not_called()


def test_dispatch_failure_retry_survives_restart_but_does_not_repeat_answer(
    tmp_path: Path,
):
    wait, command, answer = question()
    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
    manager.manage("p1")
    manager.wait_for_input(lambda: wait)
    with patch.object(
        manager, "_spawn", side_effect=OSError("environment unavailable")
    ):
        first = manager.submit_input_and_continue(command, lambda: answer)
    assert first.answer == answer and first.continuation == "retryable"
    restored = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
    with patch.object(restored, "_spawn") as spawn:
        restored.tick()  # failed dispatch waits for explicit retry
        spawn.assert_not_called()
        assert (
            restored.submit_input_and_continue(command, lambda: answer).continuation
            == "queued"
        )
        spawn.assert_called_once()
        assert restored.snapshot("p1").failure is None


@pytest.mark.parametrize("management", ["paused", "unmanaged"])
def test_input_recording_never_overwrites_another_pause(
    tmp_path: Path, management: str
):
    wait, command, answer = question()
    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
    manager._managed = {"p1": management}
    manager.wait_for_input(lambda: wait)
    with patch.object(manager, "_spawn") as spawn:
        assert (
            manager.submit_input_and_continue(command, lambda: answer).continuation
            == "blocked"
        )
        spawn.assert_not_called()


def test_old_answer_cannot_release_next_question(tmp_path: Path):
    wait, command, answer = question()
    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "waiting_for_input")
    manager.manage("p1")
    later = wait.model_copy(
        update={"step": wait.step.model_copy(update={"step_key": "choose/2"})}
    )
    manager.wait_for_input(lambda: later)
    with patch.object(manager, "_spawn") as spawn:
        assert (
            manager.submit_input_and_continue(command, lambda: answer).continuation
            == "blocked"
        )
        spawn.assert_not_called()


def test_capacity_deferred_dispatch_failure_exposes_safe_retry(tmp_path: Path):
    wait, command, answer = question()
    manager = ProjectProcedureWorkers(
        lambda: tmp_path, lambda _: "ready", max_workers=0
    )
    manager.manage("p1")
    manager.wait_for_input(lambda: wait)
    assert (
        manager.submit_input_and_continue(command, lambda: answer).continuation
        == "queued"
    )
    manager.max_workers = 1
    with patch.object(manager, "_spawn", side_effect=OSError("python missing")):
        manager.tick()
    assert manager.snapshot("p1").input_continuation_retryable
    with patch.object(manager, "_spawn") as spawn:
        assert (
            manager.submit_input_and_continue(command, lambda: answer).continuation
            == "queued"
        )
        spawn.assert_called_once()
    assert not manager.snapshot("p1").input_continuation_retryable


def test_partially_started_worker_failure_cannot_be_retried_as_dispatch(tmp_path: Path):
    wait, command, answer = question()
    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
    manager.manage("p1")
    manager.wait_for_input(lambda: wait)
    child = Mock()
    child.poll.return_value = None

    def partially_spawned(_identity: str):
        manager._children["p1"] = child
        raise OSError("registration write failed")

    with patch.object(manager, "_spawn", side_effect=partially_spawned) as spawn:
        result = manager.submit_input_and_continue(command, lambda: answer)
        assert result.continuation == "blocked"
        assert not manager.snapshot("p1").input_continuation_retryable
        assert (
            manager.submit_input_and_continue(command, lambda: answer).continuation
            == "blocked"
        )
        spawn.assert_called_once()


def test_cancellation_after_answer_before_scheduling_does_not_spawn(tmp_path: Path):
    wait, command, answer = question()
    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "closed")
    manager.manage("p1")
    manager.wait_for_input(lambda: wait)
    with patch.object(manager, "_spawn") as spawn:
        manager.submit_input_and_continue(command, lambda: answer)
        manager.tick()
        spawn.assert_not_called()
