from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import cast

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.api._config import LabConfigOperations
from scopecat.api._remote import RemoteRunOperations
from scopecat.api._runner import _DaemonRunner
from scopecat.api.procedures import LabProcedureContext, ProcedureLabSession
from scopecat.automation import (
    ConfigActivationOutputRef,
    InterpretationRequest,
    ProcedureCancelCommand,
    ProcedureCloseCommand,
    ProcedureContext,
    ProcedureControlError,
    ProcedureDefinitionRef,
    ProcedureRegistry,
    ProcedureRun,
    ProcedureRunAttentionCommand,
    ProcedureRunListQuery,
    ProcedureStepAttemptListQuery,
    ProcedureStepAttentionCommand,
    ProcedureStepAttentionRetryCommand,
    ProcedureStepBeginCommand,
    ProcedureStepCompleteCommand,
    ProcedureStepFailCommand,
    ProcedureStepInputSubmitCommand,
    ProcedureStepInputWaitCommand,
    ProcedureSubmitCommand,
    ProcedureWorker,
    ProcedureWorkerLeaseAcquireCommand,
    ProcedureWorkerLeaseHeartbeatCommand,
    ProcedureWorkerLeaseReleaseCommand,
    RunOutputRef,
    procedure,
    procedure_step_operation_id,
)
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.wire import ConfigPublishCommand, DirectConfigRevisionSource
from scopecat_testkit.workflow_fixtures import load_config

from scopecat_server import LocalDaemonRuntime

_DEFINITION_HASH = "sha256:" + "1" * 64
_FIRST_STEP_HASH = "sha256:" + "2" * 64
_SECOND_STEP_HASH = "sha256:" + "3" * 64


class _WorkerIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    child_run_id: str


class _ActivationWorkerIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: str
    expected_generation: int


class _TwoStepWorkerIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    first_run_id: str
    second_run_id: str


@dataclass(frozen=True)
class _ResonatorSelection:
    resonator: str


@dataclass(frozen=True, slots=True)
class _WorkerContext:
    durable: ProcedureContext
    effect: Callable[[str], RunOutputRef]


@dataclass(frozen=True, slots=True)
class _TwoStepWorkerContext:
    durable: ProcedureContext
    first_effect: Callable[[str], RunOutputRef]
    second_effect: Callable[[str], RunOutputRef]


def _run_one_durable_step(context: _WorkerContext, intent: _WorkerIntent) -> None:
    output = context.durable.step(
        "child/run",
        operation="run",
        intent_hash=_FIRST_STEP_HASH,
        effect=context.effect,
    )
    if output.run_id != intent.child_run_id:
        raise ValueError("durable child run does not match procedure intent")


_ONE_STEP_PROCEDURE = procedure(
    id="tests.http-one-step",
    version="1",
    intent=_WorkerIntent,
)(_run_one_durable_step)


def _run_two_durable_steps(
    context: _TwoStepWorkerContext,
    intent: _TwoStepWorkerIntent,
) -> None:
    first = context.durable.step(
        "first",
        operation="run",
        intent_hash=_FIRST_STEP_HASH,
        effect=context.first_effect,
    )
    if first.run_id != intent.first_run_id:
        raise ValueError("first durable child run does not match procedure intent")
    second = context.durable.step(
        "second",
        operation="run",
        intent_hash=_SECOND_STEP_HASH,
        effect=context.second_effect,
        inputs=(first,),
    )
    if second.run_id != intent.second_run_id:
        raise ValueError("second durable child run does not match procedure intent")


_TWO_STEP_PROCEDURE = procedure(
    id="tests.http-two-step",
    version="1",
    intent=_TwoStepWorkerIntent,
)(_run_two_durable_steps)


@procedure(
    id="tests.http-config-activation",
    version="1",
    intent=_ActivationWorkerIntent,
)
def _activate_saved_config(
    context: LabProcedureContext,
    intent: _ActivationWorkerIntent,
) -> None:
    context.activate_config_entry(
        "activate",
        intent.entry_id,
        expected_generation=intent.expected_generation,
        actor="procedure-worker",
        note="activate through a durable procedure",
    )


def _definition() -> ProcedureDefinitionRef:
    return ProcedureDefinitionRef(
        id="drag-calibration",
        version="1",
        fingerprint=_DEFINITION_HASH,
    )


def _submit(client: DaemonClient, request_key: str) -> ProcedureRun:
    return client.submit_procedure(
        ProcedureSubmitCommand(
            request_key=request_key,
            definition=_definition(),
            intent={"qubits": ["q0", "q1"]},
        )
    ).run


def test_procedure_http_round_trip_persists_pages_and_fences(
    tmp_path: Path,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        first = _submit(client, "request-1")
        second = _submit(client, "request-2")
        third = _submit(client, "request-3")

        assert _submit(client, "request-1") == first
        with pytest.raises(DaemonConflictError, match="different intent"):
            client.submit_procedure(
                ProcedureSubmitCommand(
                    request_key="request-1",
                    definition=_definition(),
                    intent={"qubits": ["q2"]},
                )
            )

        page = client.list_procedures(ProcedureRunListQuery(limit=2))
        assert page.items == (third, second)
        assert page.next_cursor is not None
        assert client.list_procedures(
            ProcedureRunListQuery(limit=2, cursor=page.next_cursor)
        ).items == (first,)

        acquired = client.acquire_procedure_worker_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=first.procedure_run_id,
                worker_id="worker-1",
                expected_run_revision=first.revision,
            )
        )
        stale_token = f"{acquired.lease.lease_token}-stale"
        with pytest.raises(DaemonConflictError, match="stale"):
            client.heartbeat_procedure_worker_lease(
                ProcedureWorkerLeaseHeartbeatCommand(
                    procedure_run_id=first.procedure_run_id,
                    lease_token=stale_token,
                )
            )
        heartbeat = client.heartbeat_procedure_worker_lease(
            ProcedureWorkerLeaseHeartbeatCommand(
                procedure_run_id=first.procedure_run_id,
                lease_token=acquired.lease.lease_token,
            )
        )
        assert heartbeat.run.revision == acquired.run.revision

        begun = client.begin_procedure_step(
            ProcedureStepBeginCommand(
                procedure_run_id=first.procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=heartbeat.run.revision,
                step_key="baseline/beta",
                operation="run",
                intent_hash=_FIRST_STEP_HASH,
            )
        )
        assert begun.operation_id.startswith("procedure-step:")
        with pytest.raises(DaemonConflictError, match="stale"):
            client.complete_procedure_step(
                ProcedureStepCompleteCommand(
                    procedure_run_id=first.procedure_run_id,
                    lease_token=stale_token,
                    expected_run_revision=begun.run.revision,
                    step_key=begun.step.step_key,
                    attempt=begun.step.attempt,
                    expected_step_revision=begun.step.revision,
                    output=RunOutputRef(run_id="run-baseline"),
                )
            )
        first_output = RunOutputRef(run_id="run-baseline")
        completed = client.complete_procedure_step(
            ProcedureStepCompleteCommand(
                procedure_run_id=first.procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=begun.run.revision,
                step_key=begun.step.step_key,
                attempt=begun.step.attempt,
                expected_step_revision=begun.step.revision,
                output=first_output,
            )
        )

        second_begun = client.begin_procedure_step(
            ProcedureStepBeginCommand(
                procedure_run_id=first.procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=completed.run.revision,
                step_key="validation",
                operation="run",
                intent_hash=_SECOND_STEP_HASH,
                inputs=(first_output,),
            )
        )
        second_completed = client.complete_procedure_step(
            ProcedureStepCompleteCommand(
                procedure_run_id=first.procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=second_begun.run.revision,
                step_key=second_begun.step.step_key,
                attempt=second_begun.step.attempt,
                expected_step_revision=second_begun.step.revision,
                output=RunOutputRef(run_id="run-validation"),
            )
        )
        step_page = client.list_procedure_step_attempts(
            first.procedure_run_id,
            ProcedureStepAttemptListQuery(limit=1),
        )
        assert step_page.items == (second_completed.step,)
        assert step_page.next_cursor is not None
        assert client.list_procedure_step_attempts(
            first.procedure_run_id,
            ProcedureStepAttemptListQuery(limit=1, cursor=step_page.next_cursor),
        ).items == (completed.step,)

        closed = client.close_procedure(
            ProcedureCloseCommand(
                procedure_run_id=first.procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=second_completed.run.revision,
                status="succeeded",
            )
        ).run
        assert closed.state == "closed"
        assert client.list_procedures(ProcedureRunListQuery(state="closed")).items == (
            closed,
        )

    with (
        LocalDaemonRuntime(tmp_path) as restarted,
        TestClient(restarted.app()) as transport,
        _daemon_client(transport) as client,
    ):
        assert client.get_procedure(first.procedure_run_id) == closed
        attempts = client.list_procedure_step_attempts(
            first.procedure_run_id,
            ProcedureStepAttemptListQuery(),
        )
        assert attempts.items == (second_completed.step, completed.step)
        with pytest.raises(DaemonConflictError, match="leased"):
            client.heartbeat_procedure_worker_lease(
                ProcedureWorkerLeaseHeartbeatCommand(
                    procedure_run_id=first.procedure_run_id,
                    lease_token=acquired.lease.lease_token,
                )
            )


@pytest.mark.parametrize("cancel", [False, True])
def test_procedure_http_round_trips_interpretation_input(
    tmp_path: Path,
    cancel: bool,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        submitted = _submit(client, "interpretation")
        acquired = client.acquire_procedure_worker_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=submitted.procedure_run_id,
                worker_id="worker-1",
                expected_run_revision=submitted.revision,
            )
        )
        schema = AnalysisFactSchema(
            "tests.resonator-selection.v1",
            _ResonatorSelection,
        )
        request = InterpretationRequest(
            title="Select resonator",
            instructions="Return the selected resonator.",
            schema_id=schema.id,
            schema_hash=schema.schema_hash,
            structure=schema.structure,
            response_template={"resonator": "replace after reviewing the trace"},
        )
        begun = client.begin_procedure_step(
            ProcedureStepBeginCommand(
                procedure_run_id=submitted.procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=acquired.run.revision,
                step_key="select-resonator",
                operation="interpretation",
                intent_hash=request.request_hash,
            )
        )
        waiting = client.wait_procedure_step_input(
            ProcedureStepInputWaitCommand(
                procedure_run_id=submitted.procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=begun.run.revision,
                step_key=begun.step.step_key,
                attempt=begun.step.attempt,
                expected_step_revision=begun.step.revision,
                request=request,
            )
        )
        assert waiting.run.state == "waiting_for_input"
        assert waiting.step.interpretation_request == request

        if cancel:
            command = ProcedureCancelCommand(
                procedure_run_id=waiting.run.procedure_run_id,
                expected_run_revision=waiting.run.revision,
                actor="reviewer",
                reason="Stop review",
            )
            closed = client.cancel_procedure(command).run
            assert closed.closure is not None and closed.closure.status == "cancelled"
            assert client.list_procedure_step_attempts(
                closed.procedure_run_id, ProcedureStepAttemptListQuery()
            ).items == (waiting.step,)
            with pytest.raises(DaemonConflictError):
                client.submit_procedure_step_input(
                    ProcedureStepInputSubmitCommand(
                        procedure_run_id=closed.procedure_run_id,
                        expected_run_revision=closed.revision,
                        step_key=waiting.step.step_key,
                        attempt=waiting.step.attempt,
                        expected_step_revision=waiting.step.revision,
                        request_hash=waiting.step.intent_hash,
                        actor="late-reviewer",
                        actor_kind="human",
                        value=schema.encode(_ResonatorSelection(resonator="r2")),
                    )
                )
            return

        answered = client.submit_procedure_step_input(
            ProcedureStepInputSubmitCommand(
                procedure_run_id=submitted.procedure_run_id,
                expected_run_revision=waiting.run.revision,
                step_key=waiting.step.step_key,
                attempt=waiting.step.attempt,
                expected_step_revision=waiting.step.revision,
                request_hash=waiting.step.intent_hash,
                actor="operator@example.test",
                actor_kind="human",
                value=schema.encode(_ResonatorSelection(resonator="r2")),
            )
        )

        assert answered.run.state == "ready"
        assert schema.decode(answered.output.response.value) == _ResonatorSelection(
            resonator="r2"
        )


def test_procedure_http_exposes_worker_state_transitions(tmp_path: Path) -> None:
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        released_run = _submit(client, "release")
        released_lease = client.acquire_procedure_worker_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=released_run.procedure_run_id,
                worker_id="release-worker",
                expected_run_revision=released_run.revision,
            )
        )
        released = client.release_procedure_worker_lease(
            ProcedureWorkerLeaseReleaseCommand(
                procedure_run_id=released_run.procedure_run_id,
                lease_token=released_lease.lease.lease_token,
                expected_run_revision=released_lease.run.revision,
            )
        ).run
        assert released.state == "ready"

        run_attention = _submit(client, "run-attention")
        run_attention_lease = client.acquire_procedure_worker_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=run_attention.procedure_run_id,
                worker_id="run-attention-worker",
                expected_run_revision=run_attention.revision,
            )
        )
        attention = client.require_procedure_run_attention(
            ProcedureRunAttentionCommand(
                procedure_run_id=run_attention.procedure_run_id,
                lease_token=run_attention_lease.lease.lease_token,
                expected_run_revision=run_attention_lease.run.revision,
                reason="definition cannot be loaded",
            )
        ).run
        assert attention.state == "attention_required"

        failed_run = _submit(client, "failed-step")
        failed_lease = client.acquire_procedure_worker_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=failed_run.procedure_run_id,
                worker_id="failure-worker",
                expected_run_revision=failed_run.revision,
            )
        )
        failed_begun = client.begin_procedure_step(
            ProcedureStepBeginCommand(
                procedure_run_id=failed_run.procedure_run_id,
                lease_token=failed_lease.lease.lease_token,
                expected_run_revision=failed_lease.run.revision,
                step_key="analysis",
                operation="run",
                intent_hash=_FIRST_STEP_HASH,
            )
        )
        failed = client.fail_procedure_step(
            ProcedureStepFailCommand(
                procedure_run_id=failed_run.procedure_run_id,
                lease_token=failed_lease.lease.lease_token,
                expected_run_revision=failed_begun.run.revision,
                step_key=failed_begun.step.step_key,
                attempt=failed_begun.step.attempt,
                expected_step_revision=failed_begun.step.revision,
                reason="known analysis failure",
            )
        )
        assert failed.run.state == "leased"
        assert failed.step.state == "failed"
        assert (
            client.close_procedure(
                ProcedureCloseCommand(
                    procedure_run_id=failed_run.procedure_run_id,
                    lease_token=failed_lease.lease.lease_token,
                    expected_run_revision=failed.run.revision,
                    status="failed",
                    reason="known analysis failure",
                )
            ).run.state
            == "closed"
        )

        quarantined_run = _submit(client, "step-attention")
        quarantined_lease = client.acquire_procedure_worker_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=quarantined_run.procedure_run_id,
                worker_id="step-attention-worker",
                expected_run_revision=quarantined_run.revision,
            )
        )
        quarantined_begun = client.begin_procedure_step(
            ProcedureStepBeginCommand(
                procedure_run_id=quarantined_run.procedure_run_id,
                lease_token=quarantined_lease.lease.lease_token,
                expected_run_revision=quarantined_lease.run.revision,
                step_key="indeterminate-child",
                operation="run",
                intent_hash=_SECOND_STEP_HASH,
            )
        )
        quarantined = client.require_procedure_step_attention(
            ProcedureStepAttentionCommand(
                procedure_run_id=quarantined_run.procedure_run_id,
                lease_token=quarantined_lease.lease.lease_token,
                expected_run_revision=quarantined_begun.run.revision,
                step_key=quarantined_begun.step.step_key,
                attempt=quarantined_begun.step.attempt,
                expected_step_revision=quarantined_begun.step.revision,
                reason="child outcome is unknown",
            )
        )
        assert quarantined.run.state == "attention_required"
        assert quarantined.step.state == "attention_required"
        retry_command = ProcedureStepAttentionRetryCommand(
            procedure_run_id=quarantined_run.procedure_run_id,
            expected_run_revision=quarantined.run.revision,
            step_key=quarantined.step.step_key,
            attempt=quarantined.step.attempt,
            expected_step_revision=quarantined.step.revision,
        )
        retried = client.retry_procedure_step_attention(retry_command)
        assert client.retry_procedure_step_attention(retry_command) == retried
        assert retried.run.state == "ready"
        assert retried.step == quarantined.step
        retry_lease = client.acquire_procedure_worker_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=quarantined_run.procedure_run_id,
                worker_id="step-retry-worker",
                expected_run_revision=retried.run.revision,
            )
        )
        retry_begun = client.begin_procedure_step(
            ProcedureStepBeginCommand(
                procedure_run_id=quarantined_run.procedure_run_id,
                lease_token=retry_lease.lease.lease_token,
                expected_run_revision=retry_lease.run.revision,
                step_key=quarantined.step.step_key,
                operation=quarantined.step.operation,
                intent_hash=quarantined.step.intent_hash,
                inputs=quarantined.step.inputs,
            )
        )
        assert retry_begun.step.attempt == quarantined.step.attempt + 1
        assert retry_begun.operation_id == quarantined_begun.operation_id


def test_daemon_client_drives_core_worker_and_replays_closed_procedure(
    tmp_path: Path,
) -> None:
    operation_ids: list[str] = []
    output = RunOutputRef(run_id="child-run-durable")

    def effect(operation_id: str) -> RunOutputRef:
        operation_ids.append(operation_id)
        return output

    def context_factory(context: ProcedureContext) -> object:
        return _WorkerContext(durable=context, effect=effect)

    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        worker = ProcedureWorker(
            client,
            ProcedureRegistry((_ONE_STEP_PROCEDURE,)),
            context_factory=context_factory,
        )
        completed = worker.execute(
            _ONE_STEP_PROCEDURE,
            {"child_run_id": output.run_id},
            "durable-worker-request",
            "http-worker-1",
        )

        assert completed.state == "closed"
        assert completed.closure is not None
        assert completed.closure.status == "succeeded"

    with (
        LocalDaemonRuntime(tmp_path) as restarted,
        TestClient(restarted.app()) as transport,
        _daemon_client(transport) as client,
    ):
        worker = ProcedureWorker(
            client,
            ProcedureRegistry((_ONE_STEP_PROCEDURE,)),
            context_factory=context_factory,
        )
        replayed = worker.execute(
            _ONE_STEP_PROCEDURE,
            {"child_run_id": output.run_id},
            "durable-worker-request",
            "http-worker-2",
        )
        page = client.list_procedure_step_attempts(
            completed.procedure_run_id,
            ProcedureStepAttemptListQuery(),
        )

        assert replayed == completed
        [step] = page.items
        assert step.state == "succeeded"
        assert step.output == output
        expected_operation_id = procedure_step_operation_id(
            step.procedure_run_id,
            step.step_key,
        )
        assert operation_ids == [expected_operation_id]


def test_core_worker_yields_over_http_and_an_exact_worker_resumes(
    tmp_path: Path,
) -> None:
    stop = Event()
    effects: list[tuple[str, str]] = []
    release_commands: list[ProcedureWorkerLeaseReleaseCommand] = []
    context_factory = _two_step_context_factory(stop=stop, effects=effects)

    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(
            transport,
            release_commands=release_commands,
        ) as client,
    ):
        submitted = client.submit_procedure(
            ProcedureSubmitCommand(
                request_key="http-yield",
                definition=_TWO_STEP_PROCEDURE.ref,
                intent=_TWO_STEP_PROCEDURE.encode_intent(
                    {
                        "first_run_id": "first-child",
                        "second_run_id": "second-child",
                    }
                ),
            )
        ).run
        yielded = ProcedureWorker(
            client,
            ProcedureRegistry((_TWO_STEP_PROCEDURE,)),
            context_factory=context_factory,
        ).resume_snapshot(
            submitted,
            worker_id="http-yield-worker-1",
            should_yield=stop.is_set,
        )

        assert yielded.state == "ready"
        assert yielded.closure is None
        assert [label for label, _ in effects] == ["first"]
        [release] = release_commands
        with pytest.raises(DaemonConflictError, match="leased"):
            client.heartbeat_procedure_worker_lease(
                ProcedureWorkerLeaseHeartbeatCommand(
                    procedure_run_id=release.procedure_run_id,
                    lease_token=release.lease_token,
                )
            )

        stop.clear()
        completed = ProcedureWorker(
            client,
            ProcedureRegistry((_TWO_STEP_PROCEDURE,)),
            context_factory=context_factory,
        ).resume_snapshot(
            yielded,
            worker_id="http-yield-worker-2",
        )

        assert completed.state == "closed"
        assert completed.closure is not None
        assert completed.closure.status == "succeeded"
        assert [label for label, _ in effects] == ["first", "second"]
        attempts = client.list_procedure_step_attempts(
            completed.procedure_run_id,
            ProcedureStepAttemptListQuery(),
        ).items
        assert tuple(item.step_key for item in attempts) == ("second", "first")
        assert all(item.state == "succeeded" for item in attempts)


def test_core_worker_recovers_after_two_lost_release_responses(
    tmp_path: Path,
) -> None:
    stop = Event()
    effects: list[tuple[str, str]] = []
    release_commands: list[ProcedureWorkerLeaseReleaseCommand] = []
    requests: list[tuple[str, str]] = []
    context_factory = _two_step_context_factory(stop=stop, effects=effects)

    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(
            transport,
            lose_procedure_release_responses=2,
            release_commands=release_commands,
            request_log=requests,
        ) as client,
    ):
        submitted = client.submit_procedure(
            ProcedureSubmitCommand(
                request_key="http-yield-response-loss",
                definition=_TWO_STEP_PROCEDURE.ref,
                intent=_TWO_STEP_PROCEDURE.encode_intent(
                    {
                        "first_run_id": "first-child",
                        "second_run_id": "second-child",
                    }
                ),
            )
        ).run
        worker = ProcedureWorker(
            client,
            ProcedureRegistry((_TWO_STEP_PROCEDURE,)),
            context_factory=context_factory,
        )

        with pytest.raises(
            ProcedureControlError,
            match="release_procedure_worker_lease",
        ):
            worker.resume_snapshot(
                submitted,
                worker_id="http-yield-lost-worker",
                should_yield=stop.is_set,
            )

        durable = client.get_procedure(submitted.procedure_run_id)
        assert durable.state == "ready"
        assert durable.closure is None
        assert [label for label, _ in effects] == ["first"]
        assert len(release_commands) == 2
        assert release_commands[0] == release_commands[1]
        release_path = (
            f"/api/v1/procedures/{submitted.procedure_run_id}/worker/lease/release"
        )
        assert requests.count(("POST", release_path)) == 2
        with pytest.raises(DaemonConflictError, match="leased"):
            client.heartbeat_procedure_worker_lease(
                ProcedureWorkerLeaseHeartbeatCommand(
                    procedure_run_id=release_commands[0].procedure_run_id,
                    lease_token=release_commands[0].lease_token,
                )
            )

    stop.clear()
    with (
        LocalDaemonRuntime(tmp_path) as restarted,
        TestClient(restarted.app()) as transport,
        _daemon_client(transport) as client,
    ):
        durable = client.get_procedure(submitted.procedure_run_id)
        completed = ProcedureWorker(
            client,
            ProcedureRegistry((_TWO_STEP_PROCEDURE,)),
            context_factory=context_factory,
        ).resume_snapshot(
            durable,
            worker_id="http-yield-restarted-worker",
        )

        assert completed.state == "closed"
        assert completed.closure is not None
        assert completed.closure.status == "succeeded"
        assert [label for label, _ in effects] == ["first", "second"]
        assert len({operation_id for _, operation_id in effects}) == 2
        attempts = client.list_procedure_step_attempts(
            completed.procedure_run_id,
            ProcedureStepAttemptListQuery(),
        ).items
        assert tuple(item.step_key for item in attempts) == ("second", "first")
        assert all(item.state == "succeeded" for item in attempts)


def test_procedure_config_activation_recovers_two_lost_http_responses(
    tmp_path: Path,
) -> None:
    config = load_config()
    requests: list[tuple[str, str]] = []
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(
            transport,
            lose_config_activation_responses=2,
            request_log=requests,
        ) as client,
    ):
        initial = client.active_config()
        target_config = config.model_copy(update={"id": "procedure-target-config"})
        target = client.publish_config(
            ConfigPublishCommand(
                operation_id="publish:procedure-target-entry",
                source=DirectConfigRevisionSource(config=target_config),
                entry_id="procedure-target-entry",
                actor="test-setup",
                expected_generation=initial.activation.generation,
            )
        )
        current_config = config.model_copy(update={"id": "procedure-current-config"})
        current = client.publish_config(
            ConfigPublishCommand(
                operation_id="publish:procedure-current-entry",
                source=DirectConfigRevisionSource(config=current_config),
                entry_id="procedure-current-entry",
                actor="test-setup",
                expected_generation=target.activation.generation,
            )
        )
        lab_config = LabConfigOperations(
            client=client,
            runs=cast("RemoteRunOperations", object()),
            default_config=None,
            operator="procedure-worker",
        )
        worker = ProcedureWorker(
            client,
            ProcedureRegistry((_activate_saved_config,)),
            context_factory=lambda durable: LabProcedureContext(
                durable,
                runner=cast("_DaemonRunner", object()),
                config=lab_config,
                session=cast("ProcedureLabSession", object()),
            ),
        )

        completed = worker.execute(
            _activate_saved_config,
            _ActivationWorkerIntent(
                entry_id=target.entry.id,
                expected_generation=current.activation.generation,
            ),
            "config-activation-response-loss",
            "http-worker-config-activation",
        )
        [step] = client.list_procedure_step_attempts(
            completed.procedure_run_id,
            ProcedureStepAttemptListQuery(),
        ).items
        operation_id = procedure_step_operation_id(
            step.procedure_run_id,
            step.step_key,
        )
        activation_path = "/api/v1/config-registry/activation-operations"
        assert requests.count(("POST", activation_path)) == 2
        assert requests.count(("GET", f"{activation_path}/{operation_id}")) == 1
        receipt = client.config_activation_operation(operation_id)
        active = client.active_config()

        assert completed.state == "closed"
        assert completed.closure is not None
        assert completed.closure.status == "succeeded"
        assert step.state == "succeeded"
        assert step.output == ConfigActivationOutputRef(
            generation=current.activation.generation + 1,
            entry_id=target.entry.id,
            entry_content_hash=target.entry.content_hash,
        )
        assert receipt.operation.operation_id == operation_id
        assert receipt.activation.generation == current.activation.generation + 1
        assert active.activation == receipt.activation
        assert active.entry == target.entry


def _two_step_context_factory(
    *,
    stop: Event,
    effects: list[tuple[str, str]],
) -> Callable[[ProcedureContext], object]:
    def first_effect(operation_id: str) -> RunOutputRef:
        effects.append(("first", operation_id))
        stop.set()
        return RunOutputRef(run_id="first-child")

    def second_effect(operation_id: str) -> RunOutputRef:
        effects.append(("second", operation_id))
        return RunOutputRef(run_id="second-child")

    def factory(durable: ProcedureContext) -> object:
        return _TwoStepWorkerContext(
            durable=durable,
            first_effect=first_effect,
            second_effect=second_effect,
        )

    return factory


def _daemon_client(
    transport: TestClient,
    *,
    lose_config_activation_responses: int = 0,
    lose_procedure_release_responses: int = 0,
    release_commands: list[ProcedureWorkerLeaseReleaseCommand] | None = None,
    request_log: list[tuple[str, str]] | None = None,
) -> DaemonClient:
    remaining_lost_config_responses = lose_config_activation_responses
    remaining_lost_release_responses = lose_procedure_release_responses

    def send(request: httpx2.Request) -> httpx2.Response:
        nonlocal remaining_lost_config_responses
        nonlocal remaining_lost_release_responses
        if request_log is not None:
            request_log.append((request.method, request.url.path))
        is_procedure_release = request.method == "POST" and request.url.path.endswith(
            "/worker/lease/release"
        )
        if is_procedure_release and release_commands is not None:
            release_commands.append(
                ProcedureWorkerLeaseReleaseCommand.model_validate_json(request.content)
            )
        response = transport.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        if (
            request.method == "POST"
            and request.url.path == "/api/v1/config-registry/activation-operations"
            and remaining_lost_config_responses > 0
        ):
            remaining_lost_config_responses -= 1
            raise httpx2.ReadError(
                "config activation response was lost",
                request=request,
            )
        if is_procedure_release and remaining_lost_release_responses > 0:
            remaining_lost_release_responses -= 1
            raise httpx2.ReadError(
                "procedure lease release response was lost",
                request=request,
            )
        return httpx2.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    return DaemonClient(
        "http://testserver",
        transport=httpx2.MockTransport(send),
    )


def test_idle_cancellation_is_atomic_and_idempotent(tmp_path: Path) -> None:
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        run = _submit(client, "cancel-ready")
        command = ProcedureCancelCommand(
            procedure_run_id=run.procedure_run_id,
            expected_run_revision=run.revision,
            actor="operator",
            reason="No longer needed",
        )
        closed = client.cancel_procedure(command).run
        assert closed.closure is not None
        assert closed.closure.status == "cancelled"
        assert closed.closure.actor == "operator"
        assert client.cancel_procedure(command).run == closed
        with pytest.raises(DaemonConflictError):
            client.acquire_procedure_worker_lease(
                ProcedureWorkerLeaseAcquireCommand(
                    procedure_run_id=closed.procedure_run_id,
                    worker_id="late-worker",
                    expected_run_revision=closed.revision,
                )
            )

        racing = _submit(client, "cancel-racing")
        acquired = client.acquire_procedure_worker_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=racing.procedure_run_id,
                worker_id="running-worker",
                expected_run_revision=racing.revision,
            )
        )
        for revision in (racing.revision, acquired.run.revision):
            with pytest.raises(DaemonConflictError):
                client.cancel_procedure(
                    ProcedureCancelCommand(
                        procedure_run_id=racing.procedure_run_id,
                        expected_run_revision=revision,
                        actor="operator",
                        reason="Stop",
                    )
                )
        assert client.get_procedure(racing.procedure_run_id).state == "leased"
