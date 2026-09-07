from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event, RLock

import pytest
from pydantic import BaseModel, ConfigDict, JsonValue

from scopecat.analysis.facts import analysis_fact_structure_hash
from scopecat.automation import (
    InterpretationOutputRef,
    InterpretationRequest,
    InterpretationResponse,
    ProcedureClosure,
    ProcedureRegistry,
    ProcedureRun,
    ProcedureStepAttempt,
    ProcedureSubmitCommand,
    ProcedureSubmitReceipt,
    ProcedureWorkerLease,
    RunOutputRef,
    procedure,
    procedure_step_operation_id,
)
from scopecat.automation.models import ProcedureStepOutputRef
from scopecat.automation.wire import (
    ProcedureCloseCommand,
    ProcedureCloseReceipt,
    ProcedureRunAttentionCommand,
    ProcedureRunAttentionReceipt,
    ProcedureStepAttentionCommand,
    ProcedureStepAttentionReceipt,
    ProcedureStepBeginCommand,
    ProcedureStepBeginReceipt,
    ProcedureStepCompleteCommand,
    ProcedureStepCompleteReceipt,
    ProcedureStepFailCommand,
    ProcedureStepFailReceipt,
    ProcedureStepInputWaitCommand,
    ProcedureStepInputWaitReceipt,
    ProcedureStepResourceWaitCommand,
    ProcedureStepResourceWaitReceipt,
    ProcedureWorkerLeaseAcquireCommand,
    ProcedureWorkerLeaseAcquireReceipt,
    ProcedureWorkerLeaseHeartbeatCommand,
    ProcedureWorkerLeaseHeartbeatReceipt,
    ProcedureWorkerLeaseReleaseCommand,
    ProcedureWorkerLeaseReleaseReceipt,
)
from scopecat.automation.worker import (
    ProcedureContext,
    ProcedureControlError,
    ProcedureLeaseLostError,
    ProcedureNeedsAttention,
    ProcedureWorker,
)

_STEP_INTENT_HASH = "sha256:" + "2" * 64


class WorkerIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    case: str


class SimulatedWorkerCrash(BaseException):
    pass


_EFFECTS: dict[str, Callable[[str], RunOutputRef]] = {}
_OUTPUTS: dict[str, RunOutputRef] = {}
_INTERPRETATION_OUTPUTS: dict[str, InterpretationOutputRef] = {}


_INTERPRETATION_STRUCTURE: JsonValue = {
    "type": "object",
    "fields": {"resonator": {"type": "string"}},
}
_INTERPRETATION_REQUEST = InterpretationRequest(
    title="Choose the resonator",
    instructions="Return one resonator label.",
    schema_id="tests.resonator-selection.v1",
    schema_hash=analysis_fact_structure_hash(_INTERPRETATION_STRUCTURE),
    structure=_INTERPRETATION_STRUCTURE,
)


def run_one_step(context: ProcedureContext, intent: WorkerIntent) -> None:
    _OUTPUTS[intent.case] = context.step(
        "measure",
        operation="run",
        intent_hash=_STEP_INTENT_HASH,
        effect=_EFFECTS[intent.case],
    )


def changed_run_one_step(context: ProcedureContext, intent: WorkerIntent) -> None:
    del context, intent


def run_two_steps(context: ProcedureContext, intent: WorkerIntent) -> None:
    first = context.step(
        "first",
        operation="run",
        intent_hash=_STEP_INTENT_HASH,
        effect=_EFFECTS[f"{intent.case}:first"],
    )
    _OUTPUTS[f"{intent.case}:first"] = first
    second = context.step(
        "second",
        operation="run",
        intent_hash=_STEP_INTENT_HASH,
        effect=_EFFECTS[f"{intent.case}:second"],
        inputs=(first,),
    )
    _OUTPUTS[f"{intent.case}:second"] = second


def run_interpretation(context: ProcedureContext, intent: WorkerIntent) -> None:
    _INTERPRETATION_OUTPUTS[intent.case] = context.interpret(
        "select-resonator",
        request=_INTERPRETATION_REQUEST,
    )


class WrappedProcedureContext:
    def __init__(self, durable: ProcedureContext) -> None:
        self.durable = durable


def run_wrapped_step(context: WrappedProcedureContext, intent: WorkerIntent) -> None:
    _OUTPUTS[intent.case] = context.durable.step(
        "measure",
        operation="run",
        intent_hash=_STEP_INTENT_HASH,
        effect=_EFFECTS[intent.case],
    )


ONE_STEP = procedure(
    id="tests.replayable-worker",
    version="1",
    intent=WorkerIntent,
)(run_one_step)
MISMATCHED_ONE_STEP = procedure(
    id="tests.replayable-worker",
    version="1",
    intent=WorkerIntent,
)(changed_run_one_step)
WRAPPED_STEP = procedure(
    id="tests.wrapped-worker",
    version="1",
    intent=WorkerIntent,
)(run_wrapped_step)
TWO_STEPS = procedure(
    id="tests.yielding-worker",
    version="1",
    intent=WorkerIntent,
)(run_two_steps)
INTERPRETATION = procedure(
    id="tests.interpretation-worker",
    version="1",
    intent=WorkerIntent,
)(run_interpretation)


class MemoryProcedureControl:
    """Small wire-faithful control plane for worker behavior tests."""

    def wait_procedure_step_resources(
        self, command: ProcedureStepResourceWaitCommand
    ) -> ProcedureStepResourceWaitReceipt:
        # Resource ownership and child cancellation are exercised by server tests.
        raise NotImplementedError("this test double has no child-run resource store")

    def __init__(self, *, heartbeat_interval: float = 1.0) -> None:
        self._lock = RLock()
        self._runs: dict[str, ProcedureRun] = {}
        self._request_runs: dict[tuple[str, str], str] = {}
        self._leases: dict[str, ProcedureWorkerLease] = {}
        self._steps: dict[tuple[str, str], ProcedureStepAttempt] = {}
        self._next_run = 1
        self._next_lease = 1
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_failure: Exception | None = None
        self.heartbeat_called = Event()
        self.lose_next_complete_receipt = False
        self.effect_complete_calls = 0
        self.close_calls = 0
        self.release_calls = 0
        self.run_attention_calls = 0

    def submit_procedure(
        self,
        command: ProcedureSubmitCommand,
    ) -> ProcedureSubmitReceipt:
        with self._lock:
            key = (command.definition.id, command.request_key)
            existing_id = self._request_runs.get(key)
            if existing_id is not None:
                existing = self._runs[existing_id]
                if existing.intent_hash != command.intent_hash:
                    raise RuntimeError("request key has different intent")
                return ProcedureSubmitReceipt(run=existing)
            procedure_run_id = f"procedure-{self._next_run}"
            self._next_run += 1
            now = datetime.now(UTC)
            run = ProcedureRun(
                procedure_run_id=procedure_run_id,
                request_key=command.request_key,
                definition=command.definition,
                intent=command.intent,
                intent_hash=command.intent_hash,
                revision=1,
                state="ready",
                created_at=now,
                updated_at=now,
            )
            self._runs[procedure_run_id] = run
            self._request_runs[key] = procedure_run_id
            return ProcedureSubmitReceipt(run=run)

    def get_procedure(self, procedure_run_id: str) -> ProcedureRun:
        with self._lock:
            return self._runs[procedure_run_id]

    def acquire_procedure_worker_lease(
        self,
        command: ProcedureWorkerLeaseAcquireCommand,
    ) -> ProcedureWorkerLeaseAcquireReceipt:
        with self._lock:
            run = self._runs[command.procedure_run_id]
            assert run.revision == command.expected_run_revision
            if run.state == "leased":
                assert self._leases[run.procedure_run_id].expires_at <= datetime.now(
                    UTC
                )
            else:
                assert run.state == "ready"
            updated = _run_state(run, state="leased")
            now = datetime.now(UTC)
            lease = ProcedureWorkerLease(
                procedure_run_id=run.procedure_run_id,
                worker_id=command.worker_id,
                lease_token=f"token-{self._next_lease}",
                issued_at=now,
                renewed_at=now,
                expires_at=now + timedelta(seconds=30),
                heartbeat_interval_seconds=self.heartbeat_interval,
            )
            self._next_lease += 1
            self._runs[run.procedure_run_id] = updated
            self._leases[run.procedure_run_id] = lease
            return ProcedureWorkerLeaseAcquireReceipt(run=updated, lease=lease)

    def heartbeat_procedure_worker_lease(
        self,
        command: ProcedureWorkerLeaseHeartbeatCommand,
    ) -> ProcedureWorkerLeaseHeartbeatReceipt:
        with self._lock:
            self.heartbeat_called.set()
            if self.heartbeat_failure is not None:
                raise self.heartbeat_failure
            run, lease = self._leased(command.procedure_run_id, command.lease_token)
            now = datetime.now(UTC)
            renewed = lease.model_copy(
                update={
                    "renewed_at": now,
                    "expires_at": now + timedelta(seconds=30),
                }
            )
            self._leases[run.procedure_run_id] = renewed
            return ProcedureWorkerLeaseHeartbeatReceipt(run=run, lease=renewed)

    def arm_heartbeat_failure(self, failure: Exception) -> None:
        with self._lock:
            self.heartbeat_called.clear()
            self.heartbeat_failure = failure

    def release_procedure_worker_lease(
        self,
        command: ProcedureWorkerLeaseReleaseCommand,
    ) -> ProcedureWorkerLeaseReleaseReceipt:
        with self._lock:
            self.release_calls += 1
            run, _ = self._leased(command.procedure_run_id, command.lease_token)
            assert run.revision == command.expected_run_revision
            updated = _run_state(run, state="ready")
            self._runs[run.procedure_run_id] = updated
            del self._leases[run.procedure_run_id]
            return ProcedureWorkerLeaseReleaseReceipt(run=updated)

    def begin_procedure_step(
        self,
        command: ProcedureStepBeginCommand,
    ) -> ProcedureStepBeginReceipt:
        with self._lock:
            run, _ = self._leased(command.procedure_run_id, command.lease_token)
            key = (run.procedure_run_id, command.step_key)
            existing = self._steps.get(key)
            if existing is not None:
                assert existing.operation == command.operation
                assert existing.intent_hash == command.intent_hash
                assert existing.inputs == command.inputs
                assert existing.state in {"running", "succeeded"}
                return ProcedureStepBeginReceipt(
                    run=run,
                    step=existing,
                    operation_id=procedure_step_operation_id(
                        run.procedure_run_id,
                        existing.step_key,
                    ),
                )
            assert run.revision == command.expected_run_revision
            now = datetime.now(UTC)
            step = ProcedureStepAttempt(
                procedure_run_id=run.procedure_run_id,
                step_key=command.step_key,
                attempt=1,
                operation=command.operation,
                intent_hash=command.intent_hash,
                inputs=command.inputs,
                revision=1,
                state="running",
                started_at=now,
                updated_at=now,
            )
            updated = _run_state(run, state="leased")
            self._steps[key] = step
            self._runs[run.procedure_run_id] = updated
            return ProcedureStepBeginReceipt(
                run=updated,
                step=step,
                operation_id=procedure_step_operation_id(
                    run.procedure_run_id,
                    step.step_key,
                ),
            )

    def complete_procedure_step(
        self,
        command: ProcedureStepCompleteCommand,
    ) -> ProcedureStepCompleteReceipt:
        with self._lock:
            self.effect_complete_calls += 1
            run, _ = self._leased(command.procedure_run_id, command.lease_token)
            assert run.revision == command.expected_run_revision
            key = (run.procedure_run_id, command.step_key)
            step = self._steps[key]
            assert step.attempt == command.attempt
            assert step.revision == command.expected_step_revision
            assert step.state == "running"
            updated_step = _step_state(step, state="succeeded", output=command.output)
            updated_run = _run_state(run, state="leased")
            self._steps[key] = updated_step
            self._runs[run.procedure_run_id] = updated_run
            if self.lose_next_complete_receipt:
                self.lose_next_complete_receipt = False
                raise RuntimeError("completion response was lost")
            return ProcedureStepCompleteReceipt(
                run=updated_run,
                step=updated_step,
            )

    def fail_procedure_step(
        self,
        command: ProcedureStepFailCommand,
    ) -> ProcedureStepFailReceipt:
        with self._lock:
            run, _ = self._leased(command.procedure_run_id, command.lease_token)
            assert run.revision == command.expected_run_revision
            key = (run.procedure_run_id, command.step_key)
            step = self._steps[key]
            assert step.revision == command.expected_step_revision
            updated_step = _step_state(
                step,
                state="failed",
                failure_reason=command.reason,
            )
            updated_run = _run_state(run, state="leased")
            self._steps[key] = updated_step
            self._runs[run.procedure_run_id] = updated_run
            return ProcedureStepFailReceipt(run=updated_run, step=updated_step)

    def require_procedure_step_attention(
        self,
        command: ProcedureStepAttentionCommand,
    ) -> ProcedureStepAttentionReceipt:
        with self._lock:
            run, _ = self._leased(command.procedure_run_id, command.lease_token)
            assert run.revision == command.expected_run_revision
            key = (run.procedure_run_id, command.step_key)
            step = self._steps[key]
            assert step.revision == command.expected_step_revision
            updated_step = _step_state(
                step,
                state="attention_required",
                attention_reason=command.reason,
            )
            updated_run = _run_state(
                run,
                state="attention_required",
                attention_reason=command.reason,
            )
            self._steps[key] = updated_step
            self._runs[run.procedure_run_id] = updated_run
            del self._leases[run.procedure_run_id]
            return ProcedureStepAttentionReceipt(
                run=updated_run,
                step=updated_step,
            )

    def wait_procedure_step_input(
        self,
        command: ProcedureStepInputWaitCommand,
    ) -> ProcedureStepInputWaitReceipt:
        with self._lock:
            run, _ = self._leased(command.procedure_run_id, command.lease_token)
            assert run.revision == command.expected_run_revision
            key = (run.procedure_run_id, command.step_key)
            step = self._steps[key]
            assert step.revision == command.expected_step_revision
            updated_step = _step_state(
                step,
                state="waiting_for_input",
                interpretation_request=command.request,
            )
            updated_run = _run_state(run, state="waiting_for_input")
            self._steps[key] = updated_step
            self._runs[run.procedure_run_id] = updated_run
            del self._leases[run.procedure_run_id]
            return ProcedureStepInputWaitReceipt(
                run=updated_run,
                step=updated_step,
            )

    def answer_interpretation(
        self,
        procedure_run_id: str,
        step_key: str,
        *,
        value: JsonValue,
    ) -> None:
        with self._lock:
            run = self._runs[procedure_run_id]
            step = self._steps[(procedure_run_id, step_key)]
            request = step.interpretation_request
            assert run.state == "waiting_for_input"
            assert step.state == "waiting_for_input" and request is not None
            output = InterpretationOutputRef(
                procedure_run_id=procedure_run_id,
                step_key=step_key,
                request_hash=request.request_hash,
                response=InterpretationResponse(
                    actor="operator@example.test",
                    actor_kind="human",
                    value=value,
                    submitted_at=datetime.now(UTC),
                ),
            )
            self._steps[(procedure_run_id, step_key)] = _step_state(
                step,
                state="succeeded",
                output=output,
                interpretation_request=request,
            )
            self._runs[procedure_run_id] = _run_state(run, state="ready")

    def require_procedure_run_attention(
        self,
        command: ProcedureRunAttentionCommand,
    ) -> ProcedureRunAttentionReceipt:
        with self._lock:
            self.run_attention_calls += 1
            run, _ = self._leased(command.procedure_run_id, command.lease_token)
            assert run.revision == command.expected_run_revision
            updated = _run_state(
                run,
                state="attention_required",
                attention_reason=command.reason,
            )
            self._runs[run.procedure_run_id] = updated
            del self._leases[run.procedure_run_id]
            return ProcedureRunAttentionReceipt(run=updated)

    def close_procedure(
        self,
        command: ProcedureCloseCommand,
    ) -> ProcedureCloseReceipt:
        with self._lock:
            self.close_calls += 1
            run, _ = self._leased(command.procedure_run_id, command.lease_token)
            assert run.revision == command.expected_run_revision
            updated = _run_state(
                run,
                state="closed",
                closure=ProcedureClosure(
                    status=command.status,
                    closed_at=_next_time(run.updated_at),
                    reason=command.reason,
                ),
            )
            self._runs[run.procedure_run_id] = updated
            del self._leases[run.procedure_run_id]
            return ProcedureCloseReceipt(run=updated)

    def step(self, procedure_run_id: str, step_key: str) -> ProcedureStepAttempt:
        with self._lock:
            return self._steps[(procedure_run_id, step_key)]

    def expire_lease(self, procedure_run_id: str) -> None:
        with self._lock:
            lease = self._leases[procedure_run_id]
            now = datetime.now(UTC)
            self._leases[procedure_run_id] = lease.model_copy(
                update={
                    "renewed_at": now - timedelta(seconds=2),
                    "expires_at": now - timedelta(seconds=1),
                }
            )

    def _leased(
        self,
        procedure_run_id: str,
        lease_token: str,
    ) -> tuple[ProcedureRun, ProcedureWorkerLease]:
        run = self._runs[procedure_run_id]
        lease = self._leases[procedure_run_id]
        assert run.state == "leased"
        assert lease.lease_token == lease_token
        return run, lease


def test_worker_executes_with_stable_operation_id_and_closes_successfully() -> None:
    control = MemoryProcedureControl()
    operation_ids: list[str] = []

    def effect(operation_id: str) -> RunOutputRef:
        operation_ids.append(operation_id)
        return RunOutputRef(run_id="child-run-1")

    _EFFECTS["success"] = effect
    worker = ProcedureWorker(control, ProcedureRegistry((ONE_STEP,)))

    run = worker.execute(ONE_STEP, {"case": "success"}, "request-1", "worker-1")

    assert run.state == "closed"
    assert run.closure is not None and run.closure.status == "succeeded"
    assert operation_ids == [
        procedure_step_operation_id(run.procedure_run_id, "measure")
    ]
    assert _OUTPUTS["success"] == RunOutputRef(run_id="child-run-1")
    assert control.step(run.procedure_run_id, "measure").state == "succeeded"


def test_worker_uses_context_factory_without_coupling_to_the_wrapper() -> None:
    control = MemoryProcedureControl()
    durable_contexts: list[ProcedureContext] = []

    def factory(context: ProcedureContext) -> object:
        durable_contexts.append(context)
        return WrappedProcedureContext(context)

    _EFFECTS["wrapped"] = lambda operation_id: RunOutputRef(
        run_id=f"child-for-{operation_id}"
    )
    worker = ProcedureWorker(
        control,
        ProcedureRegistry((WRAPPED_STEP,)),
        context_factory=factory,
    )

    run = worker.execute(
        WRAPPED_STEP,
        {"case": "wrapped"},
        "request-wrapped",
        "worker-1",
    )

    assert run.state == "closed"
    assert len(durable_contexts) == 1
    assert durable_contexts[0].procedure_run_id == run.procedure_run_id
    assert _OUTPUTS["wrapped"].run_id.startswith("child-for-procedure-step:")


def test_worker_replays_succeeded_step_without_repeating_effect() -> None:
    control = MemoryProcedureControl()
    control.lose_next_complete_receipt = True
    operation_ids: list[str] = []

    def effect(operation_id: str) -> RunOutputRef:
        operation_ids.append(operation_id)
        return RunOutputRef(run_id="child-run-replay")

    _EFFECTS["response-loss"] = effect
    worker = ProcedureWorker(control, ProcedureRegistry((ONE_STEP,)))

    with pytest.raises(ProcedureControlError, match="complete_step"):
        worker.execute(
            ONE_STEP,
            {"case": "response-loss"},
            "request-response-loss",
            "worker-1",
        )

    durable = control.get_procedure("procedure-1")
    assert durable.state == "leased"
    assert control.step(durable.procedure_run_id, "measure").state == "succeeded"
    assert control.close_calls == 0
    control.expire_lease(durable.procedure_run_id)

    resumed = worker.resume(durable.procedure_run_id, worker_id="worker-2")

    assert resumed.state == "closed"
    assert resumed.closure is not None and resumed.closure.status == "succeeded"
    assert len(operation_ids) == 1
    assert _OUTPUTS["response-loss"] == RunOutputRef(run_id="child-run-replay")


def test_worker_resumes_runnable_snapshot_with_its_exact_revision() -> None:
    control = MemoryProcedureControl()
    intent = ONE_STEP.encode_intent({"case": "snapshot"})
    submitted = control.submit_procedure(
        ProcedureSubmitCommand(
            request_key="request-snapshot",
            definition=ONE_STEP.ref,
            intent=intent,
        )
    ).run
    _EFFECTS["snapshot"] = lambda _operation_id: RunOutputRef(
        run_id="child-from-snapshot"
    )
    worker = ProcedureWorker(control, ProcedureRegistry((ONE_STEP,)))

    resumed = worker.resume_snapshot(submitted, worker_id="worker-snapshot")

    assert resumed.state == "closed"
    assert _OUTPUTS["snapshot"] == RunOutputRef(run_id="child-from-snapshot")


def test_snapshot_resume_does_not_quarantine_an_unavailable_definition() -> None:
    control = MemoryProcedureControl()
    submitted = control.submit_procedure(
        ProcedureSubmitCommand(
            request_key="request-unavailable-snapshot",
            definition=ONE_STEP.ref,
            intent=ONE_STEP.encode_intent({"case": "unavailable-snapshot"}),
        )
    ).run
    worker = ProcedureWorker(control, ProcedureRegistry())

    with pytest.raises(LookupError, match="no procedure"):
        worker.resume_snapshot(submitted, worker_id="worker-snapshot")

    assert control.get_procedure(submitted.procedure_run_id).state == "ready"
    assert control.run_attention_calls == 0


def test_worker_yields_after_checkpoint_and_an_exact_new_worker_resumes() -> None:
    control = MemoryProcedureControl()
    stop = Event()
    effects: list[str] = []

    def first_effect(_operation_id: str) -> RunOutputRef:
        effects.append("first")
        stop.set()
        return RunOutputRef(run_id="first-child")

    def second_effect(_operation_id: str) -> RunOutputRef:
        effects.append("second")
        return RunOutputRef(run_id="second-child")

    _EFFECTS["yield:first"] = first_effect
    _EFFECTS["yield:second"] = second_effect
    submitted = control.submit_procedure(
        ProcedureSubmitCommand(
            request_key="request-yield",
            definition=TWO_STEPS.ref,
            intent=TWO_STEPS.encode_intent({"case": "yield"}),
        )
    ).run

    yielded = ProcedureWorker(
        control,
        ProcedureRegistry((TWO_STEPS,)),
    ).resume_snapshot(
        submitted,
        worker_id="worker-stopping",
        should_yield=stop.is_set,
    )

    assert yielded.state == "ready"
    assert yielded.closure is None
    assert effects == ["first"]
    assert control.effect_complete_calls == 1
    assert control.release_calls == 1
    assert control.close_calls == 0
    assert control.step(yielded.procedure_run_id, "first").state == "succeeded"
    with pytest.raises(KeyError):
        control.step(yielded.procedure_run_id, "second")

    yielded_again = ProcedureWorker(
        control,
        ProcedureRegistry((TWO_STEPS,)),
    ).resume_snapshot(
        yielded,
        worker_id="worker-still-stopping",
        should_yield=stop.is_set,
    )

    assert yielded_again.state == "ready"
    assert yielded_again.revision > yielded.revision
    assert effects == ["first"]
    assert control.effect_complete_calls == 1
    assert control.release_calls == 2

    stop.clear()
    resumed = ProcedureWorker(
        control,
        ProcedureRegistry((TWO_STEPS,)),
    ).resume_snapshot(yielded_again, worker_id="worker-resuming")

    assert resumed.state == "closed"
    assert resumed.closure is not None
    assert resumed.closure.status == "succeeded"
    assert effects == ["first", "second"]
    assert control.effect_complete_calls == 2
    assert control.step(resumed.procedure_run_id, "second").state == "succeeded"


def test_worker_reuses_operation_id_when_replaying_a_running_step() -> None:
    control = MemoryProcedureControl()
    operation_ids: list[str] = []

    def effect(operation_id: str) -> RunOutputRef:
        operation_ids.append(operation_id)
        if len(operation_ids) == 1:
            raise SimulatedWorkerCrash
        return RunOutputRef(run_id="idempotently-recovered-child")

    _EFFECTS["worker-crash"] = effect
    worker = ProcedureWorker(control, ProcedureRegistry((ONE_STEP,)))

    with pytest.raises(SimulatedWorkerCrash):
        worker.execute(
            ONE_STEP,
            {"case": "worker-crash"},
            "request-worker-crash",
            "worker-1",
        )

    durable = control.get_procedure("procedure-1")
    assert control.step(durable.procedure_run_id, "measure").state == "running"
    control.expire_lease(durable.procedure_run_id)

    resumed = worker.resume(durable.procedure_run_id, worker_id="worker-2")

    assert resumed.state == "closed"
    expected_operation_id = procedure_step_operation_id(
        durable.procedure_run_id,
        "measure",
    )
    assert operation_ids == [expected_operation_id, expected_operation_id]


def test_worker_records_effect_exception_then_closes_failed() -> None:
    control = MemoryProcedureControl()

    def effect(operation_id: str) -> RunOutputRef:
        del operation_id
        raise ValueError("fit diverged")

    _EFFECTS["failure"] = effect
    worker = ProcedureWorker(control, ProcedureRegistry((ONE_STEP,)))

    with pytest.raises(ValueError, match="fit diverged"):
        worker.execute(
            ONE_STEP,
            {"case": "failure"},
            "request-failure",
            "worker-1",
        )

    run = control.get_procedure("procedure-1")
    step = control.step(run.procedure_run_id, "measure")
    assert step.state == "failed"
    assert step.failure_reason == "ValueError: fit diverged"
    assert run.state == "closed"
    assert run.closure is not None
    assert run.closure.status == "failed"
    assert run.closure.reason == "ValueError: fit diverged"


def test_worker_records_atomic_step_attention_without_closing() -> None:
    control = MemoryProcedureControl()

    def effect(operation_id: str) -> RunOutputRef:
        del operation_id
        raise ProcedureNeedsAttention("fit classification is unknown")

    _EFFECTS["attention"] = effect
    worker = ProcedureWorker(control, ProcedureRegistry((ONE_STEP,)))

    run = worker.execute(
        ONE_STEP,
        {"case": "attention"},
        "request-attention",
        "worker-1",
    )

    step = control.step(run.procedure_run_id, "measure")
    assert run.state == "attention_required"
    assert run.attention_reason == "fit classification is unknown"
    assert step.state == "attention_required"
    assert step.attention_reason == run.attention_reason
    assert control.close_calls == 0
    assert control.run_attention_calls == 0


def test_worker_waits_for_typed_input_without_a_lease_then_replays_it() -> None:
    control = MemoryProcedureControl()
    worker = ProcedureWorker(
        control,
        ProcedureRegistry((INTERPRETATION,)),
    )

    waiting = worker.execute(
        INTERPRETATION,
        {"case": "human-review"},
        "request-interpretation",
        "worker-1",
    )

    assert waiting.state == "waiting_for_input"
    step = control.step(waiting.procedure_run_id, "select-resonator")
    assert step.state == "waiting_for_input"
    assert step.interpretation_request == _INTERPRETATION_REQUEST
    assert control.close_calls == 0

    control.answer_interpretation(
        waiting.procedure_run_id,
        "select-resonator",
        value={"resonator": "r2"},
    )
    completed = worker.resume(waiting.procedure_run_id, worker_id="worker-2")

    assert completed.state == "closed"
    output = _INTERPRETATION_OUTPUTS["human-review"]
    assert output.response.value == {"resonator": "r2"}


def test_worker_fences_completion_after_background_heartbeat_loses_lease() -> None:
    control = MemoryProcedureControl(heartbeat_interval=0.001)
    failure = RuntimeError("lease token is stale")

    def effect(operation_id: str) -> RunOutputRef:
        del operation_id
        control.arm_heartbeat_failure(failure)
        assert control.heartbeat_called.wait(timeout=5)
        return RunOutputRef(run_id="must-not-be-committed")

    _EFFECTS["lease-loss"] = effect
    worker = ProcedureWorker(control, ProcedureRegistry((ONE_STEP,)))

    with pytest.raises(ProcedureLeaseLostError, match="lease token is stale"):
        worker.execute(
            ONE_STEP,
            {"case": "lease-loss"},
            "request-lease-loss",
            "worker-1",
        )

    run = control.get_procedure("procedure-1")
    assert run.state == "leased"
    assert control.step(run.procedure_run_id, "measure").state == "running"
    assert control.effect_complete_calls == 0
    assert control.close_calls == 0


@pytest.mark.parametrize(
    "registry,reason_fragment",
    [
        (ProcedureRegistry(), "no procedure"),
        (ProcedureRegistry((MISMATCHED_ONE_STEP,)), "fingerprint"),
    ],
)
def test_resume_quarantines_missing_or_mismatched_definition_without_a_step(
    registry: ProcedureRegistry,
    reason_fragment: str,
) -> None:
    control = MemoryProcedureControl()
    intent = ONE_STEP.encode_intent({"case": "unresolvable"})
    submitted = control.submit_procedure(
        ProcedureSubmitCommand(
            request_key="request-unresolvable",
            definition=ONE_STEP.ref,
            intent=intent,
        )
    ).run
    worker = ProcedureWorker(control, registry)

    run = worker.resume(submitted.procedure_run_id, worker_id="worker-1")

    assert run.state == "attention_required"
    assert run.attention_reason is not None
    assert reason_fragment in run.attention_reason
    assert control.run_attention_calls == 1
    assert control.close_calls == 0


def _run_state(
    run: ProcedureRun,
    *,
    state: str,
    attention_reason: str | None = None,
    closure: ProcedureClosure | None = None,
) -> ProcedureRun:
    return ProcedureRun.model_validate(
        {
            **run.model_dump(mode="python"),
            "revision": run.revision + 1,
            "state": state,
            "updated_at": (
                closure.closed_at if closure is not None else _next_time(run.updated_at)
            ),
            "attention_reason": attention_reason,
            "closure": closure,
        }
    )


def _step_state(
    step: ProcedureStepAttempt,
    *,
    state: str,
    output: ProcedureStepOutputRef | None = None,
    interpretation_request: InterpretationRequest | None = None,
    failure_reason: str | None = None,
    attention_reason: str | None = None,
) -> ProcedureStepAttempt:
    updated_at = _next_time(step.updated_at)
    return ProcedureStepAttempt.model_validate(
        {
            **step.model_dump(mode="python"),
            "revision": step.revision + 1,
            "state": state,
            "updated_at": updated_at,
            "finished_at": (updated_at if state in {"succeeded", "failed"} else None),
            "output": output,
            "interpretation_request": interpretation_request,
            "failure_reason": failure_reason,
            "attention_reason": attention_reason,
        }
    )


def _next_time(value: datetime) -> datetime:
    return max(datetime.now(UTC), value + timedelta(microseconds=1))
