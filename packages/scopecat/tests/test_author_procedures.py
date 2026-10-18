"""Prepared procedure identity fences reject before any submission can escape."""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import assert_type, cast
from unittest.mock import Mock, call

import httpx2
import pytest

from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.api.procedure_inputs import PendingProcedureInput
from scopecat.application.author_procedures import (
    AuthorProcedure,
    PreparedAuthorProcedure,
)
from scopecat.automation import (
    InterpretationRequest,
    ProcedureDefinitionRef,
    ProcedureRun,
    ProcedureStepAttempt,
    ProcedureStepAttemptPage,
    RunOutputRef,
)
from scopecat.automation.models import ProcedureSource, procedure_intent_hash
from scopecat.automation.wire import (
    ProcedureStepAttemptListQuery,
    ProcedureStepInputSubmitCommand,
    ProcedureSubmitCommand,
)
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.records.author_revision import AuthorRevisionRef


def test_prepared_procedure_fences_reconnection_and_rechecks_submission() -> None:
    client = Mock(spec=DaemonClient)
    client.workspace_id = "source-workspace"
    client.health.return_value = SimpleNamespace(
        project_id="store", deployment_id="app"
    )
    command = ProcedureSubmitCommand(
        request_key="ordinary-retry",
        definition=ProcedureDefinitionRef(
            id="ordinary", version="1", fingerprint="sha256:" + "1" * 64
        ),
        intent={"label": "Review"},
        source=ProcedureSource(
            workspace_id=client.workspace_id,
            code_revision=AuthorRevisionRef(content_hash="sha256:" + "2" * 64),
        ),
    )
    prepared = PreparedAuthorProcedure(client, command, "store", "app")
    other = Mock(spec=DaemonClient)
    other.workspace_id = "wrong-workspace"
    with pytest.raises(ValueError, match="another workspace"):
        prepared.reconnect(other)
    other.health.assert_not_called()
    other.workspace_id = client.workspace_id
    for project, deployment in (("other-store", "app"), ("store", "other-app")):
        other.health.return_value = SimpleNamespace(
            project_id=project, deployment_id=deployment
        )
        with pytest.raises(ValueError, match="another data store or deployment"):
            prepared.reconnect(other)
    other.submit_author_procedure.assert_not_called()
    client.submit_author_procedure.assert_not_called()

    other.health.return_value = client.health.return_value
    rebound = prepared.reconnect(other)
    assert rebound.command is command
    rebound.submit()
    other.submit_author_procedure.assert_called_once_with(command)
    # A formerly valid connection must still fail before admission if its identity
    # changes. No worker or second daemon is needed to exercise this client fence.
    other.health.return_value = SimpleNamespace(
        project_id="changed", deployment_id="app"
    )
    with pytest.raises(ValueError, match="another data store"):
        rebound.submit()
    other.submit_author_procedure.assert_called_once_with(command)


@dataclass
class _Answer:
    accepted: bool


def _pending_states() -> tuple[
    ProcedureRun, ProcedureStepAttempt, AnalysisFactSchema[_Answer]
]:
    schema = AnalysisFactSchema("review.v1", _Answer)
    definition = ProcedureDefinitionRef(
        id="review", version="1", fingerprint="sha256:" + "1" * 64
    )
    run = ProcedureRun(
        procedure_run_id="review-1",
        request_key="submission-1",
        definition=definition,
        intent={},
        intent_hash=procedure_intent_hash(definition, {}),
        revision=4,
        state="waiting_for_input",
    )
    request = InterpretationRequest(
        title="Accept this run?",
        instructions="Check the retained evidence",
        schema_id=schema.id,
        schema_hash=schema.schema_hash,
        structure=schema.structure,
        metadata={"view": {"label": "original"}},
    )
    step = ProcedureStepAttempt(
        procedure_run_id=run.procedure_run_id,
        step_key="review",
        attempt=2,
        operation="interpretation",
        intent_hash=request.request_hash,
        revision=3,
        state="waiting_for_input",
        interpretation_request=request,
        inputs=(RunOutputRef(run_id="run-1"),),
    )
    return run, step, schema


@pytest.mark.parametrize("step_key", [None, "review"])
def test_pending_input_captures_question_schema_evidence_and_submission_fence(
    step_key: str | None,
) -> None:
    run, step, schema = _pending_states()
    client = Mock(spec=DaemonClient)
    client.get_procedure.return_value = run
    client.list_procedure_step_attempts.side_effect = [
        ProcedureStepAttemptPage(
            procedure_run_id=run.procedure_run_id, items=(), next_cursor=5
        ),
        ProcedureStepAttemptPage(procedure_run_id=run.procedure_run_id, items=(step,)),
    ]
    pending = assert_type(
        AuthorProcedure(client, run.procedure_run_id).pending_input(
            step_key, schema=schema
        ),
        PendingProcedureInput[_Answer],
    )
    original_hash = pending.request.request_hash
    assert client.list_procedure_step_attempts.call_args_list == [
        call(run.procedure_run_id, ProcedureStepAttemptListQuery(limit=200)),
        call(run.procedure_run_id, ProcedureStepAttemptListQuery(limit=200, cursor=5)),
    ]
    assert pending.inputs == (RunOutputRef(run_id="run-1"),)
    # Incoming models and outgoing display views are detached, including nested JSON.
    assert step.interpretation_request is not None
    step.interpretation_request.metadata["view"] = {"label": "changed"}
    pending.request.metadata["view"] = {"label": "editor"}
    assert isinstance(schema.structure, dict)
    schema.structure["caller_mutation"] = True
    assert pending.request.request_hash == original_hash
    assert pending.request.metadata["view"] == {"label": "original"}
    client.reset_mock()
    value = _Answer(True)
    receipt = pending.respond(value, actor="operator", note="reviewed")
    assert receipt is client.submit_procedure_step_input.return_value
    first = client.submit_procedure_step_input.call_args.args[0]
    assert first == ProcedureStepInputSubmitCommand(
        procedure_run_id=run.procedure_run_id,
        expected_run_revision=4,
        step_key="review",
        attempt=2,
        expected_step_revision=3,
        request_hash=original_hash,
        actor="operator",
        actor_kind="human",
        value={"accepted": True},
        note="reviewed",
    )
    pending.respond(value, actor="operator", note="reviewed")
    assert client.submit_procedure_step_input.call_args.args[0] == first
    assert [call[0] for call in client.method_calls] == [
        "submit_procedure_step_input",
        "submit_procedure_step_input",
    ]


def test_pending_response_conflict_does_not_refresh_or_retry() -> None:
    run, step, schema = _pending_states()
    client = Mock(spec=DaemonClient)
    client.get_procedure.return_value = run
    client.list_procedure_step_attempts.return_value = ProcedureStepAttemptPage(
        procedure_run_id=run.procedure_run_id, items=(step,)
    )
    pending = AuthorProcedure(client, run.procedure_run_id).pending_input(schema=schema)
    client.reset_mock()
    conflict = DaemonConflictError(
        "procedure interpretation request changed", response=httpx2.Response(409)
    )
    client.submit_procedure_step_input.side_effect = conflict
    with pytest.raises(DaemonConflictError) as raised:
        pending.respond(_Answer(True), actor="operator")
    assert raised.value is conflict
    assert [call[0] for call in client.method_calls] == ["submit_procedure_step_input"]


def test_pending_input_rejects_wrong_schema_and_answer_before_submission() -> None:
    run, step, schema = _pending_states()
    client = Mock(spec=DaemonClient)
    with pytest.raises(ValueError, match="schema does not match"):
        PendingProcedureInput(
            client, run, step, AnalysisFactSchema("other.v1", _Answer)
        )
    pending = PendingProcedureInput(client, run, step, schema)
    with pytest.raises(TypeError):
        pending.respond(
            cast("_Answer", cast("object", {"accepted": True})), actor="operator"
        )
    assert not client.method_calls


def test_pending_input_does_not_fall_back_to_older_waiting_attempt() -> None:
    run, step, schema = _pending_states()
    client = Mock(spec=DaemonClient)
    client.get_procedure.return_value = run
    client.list_procedure_step_attempts.return_value = ProcedureStepAttemptPage(
        procedure_run_id=run.procedure_run_id,
        items=(step.model_copy(update={"state": "running", "attempt": 3}), step),
    )
    with pytest.raises(ValueError, match="not a waiting interpretation"):
        AuthorProcedure(client, run.procedure_run_id).pending_input(
            "review", schema=schema
        )
    client.submit_procedure_step_input.assert_not_called()


@pytest.mark.parametrize("other_waiting", [False, True])
def test_current_pending_input_checks_all_pages_and_latest_attempts(
    other_waiting: bool,
) -> None:
    run, step, schema = _pending_states()
    client = Mock(spec=DaemonClient)
    client.get_procedure.return_value = run
    old = step.model_copy(update={"step_key": "old-question"})
    latest = old.model_copy(update={"attempt": 3, "state": "running"})
    other = step.model_copy(update={"step_key": "other-question"})
    client.list_procedure_step_attempts.side_effect = [
        ProcedureStepAttemptPage(
            procedure_run_id=run.procedure_run_id,
            items=(step, latest),
            next_cursor=5,
        ),
        ProcedureStepAttemptPage(
            procedure_run_id=run.procedure_run_id,
            items=(old, other) if other_waiting else (old,),
        ),
    ]
    task = AuthorProcedure(client, run.procedure_run_id)
    if other_waiting:
        with pytest.raises(ValueError, match="multiple current waiting inputs"):
            task.pending_input(schema=schema)
    else:
        pending = assert_type(
            task.pending_input(schema=schema), PendingProcedureInput[_Answer]
        )
        assert pending.request == step.interpretation_request
    assert client.list_procedure_step_attempts.call_count == 2
    client.submit_procedure_step_input.assert_not_called()
    client.dispatch_project_procedure.assert_not_called()


@pytest.mark.parametrize("older_waiting", [False, True])
def test_current_pending_input_rejects_missing_current_question(
    older_waiting: bool,
) -> None:
    run, step, schema = _pending_states()
    client = Mock(spec=DaemonClient)
    client.get_procedure.return_value = run
    client.list_procedure_step_attempts.side_effect = [
        ProcedureStepAttemptPage(
            procedure_run_id=run.procedure_run_id,
            items=(step.model_copy(update={"attempt": 3, "state": "running"}),),
            next_cursor=5,
        ),
        ProcedureStepAttemptPage(
            procedure_run_id=run.procedure_run_id,
            items=(step,) if older_waiting else (),
        ),
    ]
    with pytest.raises(ValueError, match="no current waiting input"):
        AuthorProcedure(client, run.procedure_run_id).pending_input(schema=schema)
    client.submit_procedure_step_input.assert_not_called()


def test_current_pending_input_validates_schema_without_selecting_by_it() -> None:
    run, step, schema = _pending_states()
    client = Mock(spec=DaemonClient)
    client.get_procedure.return_value = run
    client.list_procedure_step_attempts.return_value = ProcedureStepAttemptPage(
        procedure_run_id=run.procedure_run_id, items=(step,)
    )
    task = AuthorProcedure(client, run.procedure_run_id)
    with pytest.raises(ValueError, match="schema does not match"):
        task.pending_input(schema=AnalysisFactSchema("other.v1", _Answer))
    client.list_procedure_step_attempts.return_value = ProcedureStepAttemptPage(
        procedure_run_id=run.procedure_run_id,
        items=(step.model_copy(update={"step_key": "another"}), step),
    )
    with pytest.raises(ValueError, match="multiple current waiting inputs"):
        task.pending_input(schema=AnalysisFactSchema("other.v1", _Answer))
    client.get_procedure.return_value = run.model_copy(update={"state": "ready"})
    client.reset_mock()
    with pytest.raises(ValueError, match="not waiting for interpretation input"):
        task.pending_input(schema=schema)
    client.list_procedure_step_attempts.assert_not_called()
    client.submit_procedure_step_input.assert_not_called()


def test_legacy_response_reuses_typed_validation_without_dispatch() -> None:
    from scopecat.api.procedures import LabProcedureOperations, ProcedureHandle
    from scopecat.automation import ProcedureRegistry, ProcedureScheduleRegistry

    run, step, schema = _pending_states()
    client = Mock(spec=DaemonClient)
    client.get_procedure.return_value = run
    client.list_procedure_step_attempts.return_value = ProcedureStepAttemptPage(
        procedure_run_id=run.procedure_run_id, items=(step,)
    )
    client.submit_procedure_step_input.return_value = SimpleNamespace(run=run)
    operations = LabProcedureOperations(
        client=client,
        runner=Mock(),
        config=Mock(),
        session=Mock(),
        registry=ProcedureRegistry(),
        schedule_registry=ProcedureScheduleRegistry(),
    )
    handle = ProcedureHandle(operations, run.procedure_run_id)
    assert (
        handle.respond("review", _Answer(True), schema=schema, actor="operator").id
        == handle.id
    )
    client.submit_procedure_step_input.assert_called_once()
    client.dispatch_project_procedure.assert_not_called()
    with pytest.raises(ValueError, match="schema does not match"):
        handle.respond(
            "review",
            _Answer(True),
            schema=AnalysisFactSchema("other", _Answer),
            actor="operator",
        )
    client.submit_procedure_step_input.assert_called_once()
