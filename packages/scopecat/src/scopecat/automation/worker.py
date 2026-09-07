"""Replayable, domain-neutral execution for durable procedures."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event, Lock, Thread
from typing import Protocol, cast

from scopecat.automation.definition import ProcedureRegistry, RegisteredProcedure
from scopecat.automation.interpretations import InterpretationRequest
from scopecat.automation.models import (
    InterpretationOutputRef,
    ProcedureCloseStatus,
    ProcedureRun,
    ProcedureStepOperation,
    ProcedureStepOutputRef,
)
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
    ProcedureSubmitCommand,
    ProcedureSubmitReceipt,
    ProcedureWorkerLease,
    ProcedureWorkerLeaseAcquireCommand,
    ProcedureWorkerLeaseAcquireReceipt,
    ProcedureWorkerLeaseHeartbeatCommand,
    ProcedureWorkerLeaseHeartbeatReceipt,
    ProcedureWorkerLeaseReleaseCommand,
    ProcedureWorkerLeaseReleaseReceipt,
)
from scopecat.records.content import Sha256ContentHash
from scopecat.records.sample import SampleSelector


class ProcedureControl(Protocol):
    """Control-plane operations required by a procedure worker."""

    def submit_procedure(
        self,
        command: ProcedureSubmitCommand,
    ) -> ProcedureSubmitReceipt: ...

    def get_procedure(self, procedure_run_id: str) -> ProcedureRun: ...

    def wait_procedure_step_resources(
        self, command: ProcedureStepResourceWaitCommand
    ) -> ProcedureStepResourceWaitReceipt: ...

    def acquire_procedure_worker_lease(
        self,
        command: ProcedureWorkerLeaseAcquireCommand,
    ) -> ProcedureWorkerLeaseAcquireReceipt: ...

    def heartbeat_procedure_worker_lease(
        self,
        command: ProcedureWorkerLeaseHeartbeatCommand,
    ) -> ProcedureWorkerLeaseHeartbeatReceipt: ...

    def release_procedure_worker_lease(
        self,
        command: ProcedureWorkerLeaseReleaseCommand,
    ) -> ProcedureWorkerLeaseReleaseReceipt: ...

    def begin_procedure_step(
        self,
        command: ProcedureStepBeginCommand,
    ) -> ProcedureStepBeginReceipt: ...

    def complete_procedure_step(
        self,
        command: ProcedureStepCompleteCommand,
    ) -> ProcedureStepCompleteReceipt: ...

    def fail_procedure_step(
        self,
        command: ProcedureStepFailCommand,
    ) -> ProcedureStepFailReceipt: ...

    def require_procedure_step_attention(
        self,
        command: ProcedureStepAttentionCommand,
    ) -> ProcedureStepAttentionReceipt: ...

    def wait_procedure_step_input(
        self,
        command: ProcedureStepInputWaitCommand,
    ) -> ProcedureStepInputWaitReceipt: ...

    def require_procedure_run_attention(
        self,
        command: ProcedureRunAttentionCommand,
    ) -> ProcedureRunAttentionReceipt: ...

    def close_procedure(
        self,
        command: ProcedureCloseCommand,
    ) -> ProcedureCloseReceipt: ...


class ProcedureControlError(RuntimeError):
    """A control-plane request whose durable result is not known locally."""

    def __init__(self, operation: str, cause: Exception) -> None:
        self.operation = operation
        self.cause = cause
        super().__init__(f"procedure control operation {operation!r} failed: {cause}")


class ProcedureLeaseLostError(RuntimeError):
    """The background heartbeat proved that this worker lost authority."""

    def __init__(self, lease: ProcedureWorkerLease, cause: Exception) -> None:
        self.lease = lease
        self.cause = cause
        super().__init__(
            f"procedure lease for {lease.procedure_run_id!r} was lost: {cause}"
        )


class ProcedureNeedsAttention(RuntimeError):
    """Ask the worker to quarantine an unknown or unsafe procedure outcome."""

    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("procedure attention reason must be non-empty")
        self.reason = reason
        super().__init__(reason)


class _ProcedureAttentionRecorded(Exception):
    __slots__ = ("run",)

    run: ProcedureRun

    def __init__(self, run: ProcedureRun) -> None:
        self.run = run
        super().__init__(run.attention_reason)


class _ProcedureInputRecorded(Exception):
    __slots__ = ("run",)

    run: ProcedureRun

    def __init__(self, run: ProcedureRun) -> None:
        self.run = run
        super().__init__("procedure is waiting for interpretation input")


class _ProcedureCancellationRequested(BaseException):
    """Stop at a settled durable boundary without running another effect."""


class ProcedureWaitResources(Exception):
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"waiting for resources for {run_id}")


class _ProcedureResourcesRecorded(BaseException):
    def __init__(self, run: ProcedureRun) -> None:
        self.run = run
        super().__init__("procedure is waiting for resources")


class _ProcedureYieldRequested(BaseException):
    """Leave execution before starting another durable step."""


class ProcedureContext:
    """Lease-fenced primitive used by imperative procedure definitions."""

    __slots__ = ("_authority", "_control", "_samples", "_should_yield")

    def __init__(
        self,
        control: ProcedureControl,
        authority: _ProcedureLeaseAuthority,
        *,
        samples: tuple[SampleSelector, ...] = (),
        should_yield: Callable[[], bool] | None = None,
    ) -> None:
        self._control = control
        self._authority = authority
        self._samples = samples
        self._should_yield = should_yield

    @property
    def procedure_run_id(self) -> str:
        """Return the durable invocation identity owned by this context."""

        return self._authority.procedure_run_id

    @property
    def samples(self) -> tuple[SampleSelector, ...]:
        """Return the immutable sample scope inherited by child runs."""

        return self._samples

    def step[OutputT: ProcedureStepOutputRef](
        self,
        step_key: str,
        *,
        operation: ProcedureStepOperation,
        intent_hash: Sha256ContentHash,
        effect: Callable[[str], OutputT],
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
    ) -> OutputT:
        """Replay or execute one stable, intent-identified side-effect step."""

        if self._should_yield is not None and self._should_yield():
            raise _ProcedureYieldRequested

        begun = self._authority.fenced_call(
            "begin_step",
            lambda run, lease: self._control.begin_procedure_step(
                ProcedureStepBeginCommand(
                    procedure_run_id=run.procedure_run_id,
                    lease_token=lease.lease_token,
                    expected_run_revision=run.revision,
                    step_key=step_key,
                    operation=operation,
                    intent_hash=intent_hash,
                    inputs=inputs,
                )
            ),
        )
        if begun.step.state == "succeeded":
            output = begun.step.output
            if output is None:
                raise RuntimeError("successful procedure step has no output")
            return cast("OutputT", output)
        if begun.step.state != "running":
            raise RuntimeError(
                f"procedure step {step_key!r} cannot execute from "
                f"state {begun.step.state!r}"
            )

        self._authority.require_live()
        try:
            output = effect(begun.operation_id)
        except ProcedureWaitResources as error:
            child_run_id = error.run_id
            receipt = self._authority.fenced_call(
                "wait_resources",
                lambda run, lease: self._control.wait_procedure_step_resources(
                    ProcedureStepResourceWaitCommand(
                        procedure_run_id=run.procedure_run_id,
                        lease_token=lease.lease_token,
                        expected_run_revision=run.revision,
                        step_key=begun.step.step_key,
                        attempt=begun.step.attempt,
                        expected_step_revision=begun.step.revision,
                        run_id=child_run_id,
                    )
                ),
            )
            raise _ProcedureResourcesRecorded(receipt.run) from error
        except ProcedureNeedsAttention as error:
            receipt = self._record_step_attention(begun, error.reason)
            raise _ProcedureAttentionRecorded(receipt.run) from error
        except Exception as error:
            self._record_step_failure(begun, _exception_reason(error))
            raise

        completed = self._authority.fenced_call(
            "complete_step",
            lambda run, lease: self._control.complete_procedure_step(
                ProcedureStepCompleteCommand(
                    procedure_run_id=run.procedure_run_id,
                    lease_token=lease.lease_token,
                    expected_run_revision=run.revision,
                    step_key=begun.step.step_key,
                    attempt=begun.step.attempt,
                    expected_step_revision=begun.step.revision,
                    output=output,
                )
            ),
        )
        if completed.run.cancellation is not None:
            raise _ProcedureCancellationRequested
        durable_output = completed.step.output
        if durable_output is None:
            raise RuntimeError("completed procedure step has no output")
        return cast("OutputT", durable_output)

    def interpret(
        self,
        step_key: str,
        *,
        request: InterpretationRequest,
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
    ) -> InterpretationOutputRef:
        """Replay a typed response or durably wait without retaining the lease."""

        if self._should_yield is not None and self._should_yield():
            raise _ProcedureYieldRequested
        begun = self._authority.fenced_call(
            "begin_interpretation",
            lambda run, lease: self._control.begin_procedure_step(
                ProcedureStepBeginCommand(
                    procedure_run_id=run.procedure_run_id,
                    lease_token=lease.lease_token,
                    expected_run_revision=run.revision,
                    step_key=step_key,
                    operation="interpretation",
                    intent_hash=request.request_hash,
                    inputs=inputs,
                )
            ),
        )
        if begun.step.state == "succeeded":
            output = begun.step.output
            if not isinstance(output, InterpretationOutputRef):
                raise RuntimeError("successful interpretation has no typed output")
            return output
        if begun.step.state != "running":
            raise RuntimeError(
                f"interpretation step {step_key!r} cannot execute from "
                f"state {begun.step.state!r}"
            )
        receipt = self._authority.fenced_call(
            "wait_for_input",
            lambda run, lease: self._control.wait_procedure_step_input(
                ProcedureStepInputWaitCommand(
                    procedure_run_id=run.procedure_run_id,
                    lease_token=lease.lease_token,
                    expected_run_revision=run.revision,
                    step_key=begun.step.step_key,
                    attempt=begun.step.attempt,
                    expected_step_revision=begun.step.revision,
                    request=request,
                )
            ),
        )
        raise _ProcedureInputRecorded(receipt.run)

    def _record_step_failure(
        self,
        begun: ProcedureStepBeginReceipt,
        reason: str,
    ) -> ProcedureStepFailReceipt:
        return self._authority.fenced_call(
            "fail_step",
            lambda run, lease: self._control.fail_procedure_step(
                ProcedureStepFailCommand(
                    procedure_run_id=run.procedure_run_id,
                    lease_token=lease.lease_token,
                    expected_run_revision=run.revision,
                    step_key=begun.step.step_key,
                    attempt=begun.step.attempt,
                    expected_step_revision=begun.step.revision,
                    reason=reason,
                )
            ),
        )

    def _record_step_attention(
        self,
        begun: ProcedureStepBeginReceipt,
        reason: str,
    ) -> ProcedureStepAttentionReceipt:
        return self._authority.fenced_call(
            "require_step_attention",
            lambda run, lease: self._control.require_procedure_step_attention(
                ProcedureStepAttentionCommand(
                    procedure_run_id=run.procedure_run_id,
                    lease_token=lease.lease_token,
                    expected_run_revision=run.revision,
                    step_key=begun.step.step_key,
                    attempt=begun.step.attempt,
                    expected_step_revision=begun.step.revision,
                    reason=reason,
                )
            ),
        )


def _identity_context(context: ProcedureContext) -> object:
    return context


class ProcedureWorker:
    """Submit and replay imperative procedure definitions from durable checkpoints."""

    __slots__ = ("_context_factory", "_control", "_registry")

    def __init__(
        self,
        control: ProcedureControl,
        registry: ProcedureRegistry | None = None,
        *,
        context_factory: Callable[[ProcedureContext], object] = _identity_context,
    ) -> None:
        self._control = control
        self._registry = registry if registry is not None else ProcedureRegistry()
        self._context_factory = context_factory

    def execute(
        self,
        definition: RegisteredProcedure,
        intent: object,
        request_key: str,
        worker_id: str,
        *,
        samples: tuple[SampleSelector, ...] = (),
    ) -> ProcedureRun:
        """Idempotently submit one invocation and execute it when runnable."""

        command = ProcedureSubmitCommand(
            request_key=request_key,
            definition=definition.ref,
            intent=definition.encode_intent(intent),
            samples=samples,
        )
        submitted = _control_call(
            "submit_procedure",
            lambda: self._control.submit_procedure(command),
        )
        if (
            submitted.run.definition != command.definition
            or submitted.run.intent_hash != command.intent_hash
        ):
            raise ValueError("procedure submission receipt does not match its command")
        return self._execute_run(submitted.run, definition, worker_id=worker_id)

    def resume(
        self,
        procedure_run_id: str,
        worker_id: str = "procedure-worker",
    ) -> ProcedureRun:
        """Resolve and resume the exact definition pinned by one durable run."""

        run = _control_call(
            "get_procedure",
            lambda: self._control.get_procedure(procedure_run_id),
        )
        if run.state not in {"ready", "leased"}:
            return run
        try:
            definition = self._registry.resolve(run.definition)
        except (LookupError, ValueError) as error:
            return self._require_unresolvable_definition_attention(
                run,
                worker_id=worker_id,
                error=error,
            )
        return self._execute_run(run, definition, worker_id=worker_id)

    def resume_snapshot(
        self,
        run: ProcedureRun,
        *,
        worker_id: str,
        should_yield: Callable[[], bool] | None = None,
    ) -> ProcedureRun:
        """Resume a runnable snapshot filtered for this exact local registry."""

        if run.state not in {"ready", "leased"}:
            return run
        definition = self._registry.resolve(run.definition)
        return self._execute_run(
            run,
            definition,
            worker_id=worker_id,
            should_yield=should_yield,
        )

    def _execute_run(
        self,
        run: ProcedureRun,
        definition: RegisteredProcedure,
        *,
        worker_id: str,
        should_yield: Callable[[], bool] | None = None,
    ) -> ProcedureRun:
        if run.state not in {"ready", "leased"}:
            return run
        acquired = _control_call(
            "acquire_procedure_worker_lease",
            lambda: self._control.acquire_procedure_worker_lease(
                ProcedureWorkerLeaseAcquireCommand(
                    procedure_run_id=run.procedure_run_id,
                    worker_id=worker_id,
                    expected_run_revision=run.revision,
                )
            ),
        )
        authority = _ProcedureLeaseAuthority(self._control, acquired)
        context = ProcedureContext(
            self._control,
            authority,
            samples=acquired.run.samples,
            should_yield=should_yield,
        )
        authority.start()
        try:
            try:
                selected_context = self._context_factory(context)
                definition.run(selected_context, acquired.run.intent)
            except _ProcedureCancellationRequested:
                return self._close(
                    authority, status="cancelled", reason="Cancellation requested"
                )
            except _ProcedureYieldRequested:
                return self._release(authority)
            except _ProcedureAttentionRecorded as recorded:
                return recorded.run
            except _ProcedureInputRecorded as recorded:
                return recorded.run
            except _ProcedureResourcesRecorded as recorded:
                return recorded.run
            except ProcedureNeedsAttention as error:
                return self._require_run_attention(authority, error.reason)
            except ProcedureControlError, ProcedureLeaseLostError:
                raise
            except Exception as error:
                self._close(
                    authority,
                    status="failed",
                    reason=_exception_reason(error),
                )
                raise
            return self._close(authority, status="succeeded")
        finally:
            authority.close()

    def _require_unresolvable_definition_attention(
        self,
        run: ProcedureRun,
        *,
        worker_id: str,
        error: Exception,
    ) -> ProcedureRun:
        acquired = _control_call(
            "acquire_procedure_worker_lease",
            lambda: self._control.acquire_procedure_worker_lease(
                ProcedureWorkerLeaseAcquireCommand(
                    procedure_run_id=run.procedure_run_id,
                    worker_id=worker_id,
                    expected_run_revision=run.revision,
                )
            ),
        )
        authority = _ProcedureLeaseAuthority(self._control, acquired)
        authority.start()
        try:
            reason = f"procedure definition cannot be resolved exactly: {error}"
            return self._require_run_attention(authority, reason)
        finally:
            authority.close()

    def _require_run_attention(
        self,
        authority: _ProcedureLeaseAuthority,
        reason: str,
    ) -> ProcedureRun:
        receipt = authority.fenced_call(
            "require_run_attention",
            lambda run, lease: self._control.require_procedure_run_attention(
                ProcedureRunAttentionCommand(
                    procedure_run_id=run.procedure_run_id,
                    lease_token=lease.lease_token,
                    expected_run_revision=run.revision,
                    reason=reason,
                )
            ),
        )
        return receipt.run

    def _release(self, authority: _ProcedureLeaseAuthority) -> ProcedureRun:
        receipt = authority.fenced_call(
            "release_procedure_worker_lease",
            lambda run, lease: self._control.release_procedure_worker_lease(
                ProcedureWorkerLeaseReleaseCommand(
                    procedure_run_id=run.procedure_run_id,
                    lease_token=lease.lease_token,
                    expected_run_revision=run.revision,
                )
            ),
        )
        return receipt.run

    def _close(
        self,
        authority: _ProcedureLeaseAuthority,
        *,
        status: ProcedureCloseStatus,
        reason: str | None = None,
    ) -> ProcedureRun:
        receipt = authority.fenced_call(
            "close",
            lambda run, lease: self._control.close_procedure(
                ProcedureCloseCommand(
                    procedure_run_id=run.procedure_run_id,
                    lease_token=lease.lease_token,
                    expected_run_revision=run.revision,
                    status=status,
                    reason=reason,
                )
            ),
        )
        return receipt.run


type _FencedReceipt = (
    ProcedureStepBeginReceipt
    | ProcedureStepCompleteReceipt
    | ProcedureStepFailReceipt
    | ProcedureStepAttentionReceipt
    | ProcedureStepInputWaitReceipt
    | ProcedureStepResourceWaitReceipt
    | ProcedureRunAttentionReceipt
    | ProcedureCloseReceipt
    | ProcedureWorkerLeaseReleaseReceipt
)


class _ProcedureLeaseAuthority:
    """Serialize fenced writes with background renewal of one worker lease."""

    __slots__ = (
        "_command_lock",
        "_control",
        "_failure",
        "_lease",
        "_run",
        "_state_lock",
        "_stop",
        "_thread",
    )

    def __init__(
        self,
        control: ProcedureControl,
        acquired: ProcedureWorkerLeaseAcquireReceipt,
    ) -> None:
        self._control = control
        self._run = acquired.run
        self._lease = acquired.lease
        self._failure: tuple[ProcedureWorkerLease, Exception] | None = None
        self._stop = Event()
        self._state_lock = Lock()
        self._command_lock = Lock()
        self._thread: Thread | None = None

    @property
    def procedure_run_id(self) -> str:
        with self._state_lock:
            return self._run.procedure_run_id

    def start(self) -> None:
        self._thread = Thread(
            target=self._heartbeat_loop,
            name=f"scopecat-procedure-lease-{self.procedure_run_id}",
            daemon=True,
        )
        self._thread.start()

    def require_live(self) -> None:
        with self._state_lock:
            failure = self._failure
        if failure is not None:
            lease, cause = failure
            raise ProcedureLeaseLostError(lease, cause) from cause

    def fenced_call[ReceiptT: _FencedReceipt](
        self,
        operation: str,
        call: Callable[[ProcedureRun, ProcedureWorkerLease], ReceiptT],
    ) -> ReceiptT:
        with self._command_lock:
            self.require_live()
            with self._state_lock:
                run = self._run
                lease = self._lease
            if (
                operation in {"begin_step", "begin_interpretation"}
                and run.cancellation is not None
            ):
                raise _ProcedureCancellationRequested
            try:
                receipt = _control_call(operation, lambda: call(run, lease))
            except ProcedureControlError:
                # A cancellation request is the one external write allowed while
                # this lease is live. Retry only that exact revision transition;
                # never re-execute the effect or relax ordinary stale-write checks.
                latest = self._control.get_procedure(run.procedure_run_id)
                if (
                    latest.closure is not None
                    and latest.closure.status == "cancelled"
                    and latest.resource_wait is not None
                ):
                    raise _ProcedureResourcesRecorded(latest) from None
                if not (
                    latest.state == "leased"
                    and latest.cancellation is not None
                    and latest.revision == run.revision + 1
                    and latest.cancellation.requested_revision == latest.revision
                ):
                    raise
                self._adopt(latest)
                if operation in {"begin_step", "begin_interpretation"}:
                    raise _ProcedureCancellationRequested from None
                receipt = _control_call(operation, lambda: call(latest, lease))
            self._adopt(receipt.run)
            return receipt

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join()

    def _heartbeat_loop(self) -> None:
        while True:
            with self._state_lock:
                lease = self._lease
            if self._stop.wait(_heartbeat_delay(lease)):
                return
            with self._command_lock:
                if self._stop.is_set():
                    return
                with self._state_lock:
                    lease = self._lease
                try:
                    receipt = self._control.heartbeat_procedure_worker_lease(
                        ProcedureWorkerLeaseHeartbeatCommand(
                            procedure_run_id=lease.procedure_run_id,
                            lease_token=lease.lease_token,
                        )
                    )
                except Exception as error:
                    with self._state_lock:
                        self._failure = (lease, error)
                    return
                with self._state_lock:
                    self._run = receipt.run
                    self._lease = receipt.lease

    def _adopt(self, run: ProcedureRun) -> None:
        with self._state_lock:
            if run.procedure_run_id != self._run.procedure_run_id:
                raise ValueError("procedure control receipt belongs to another run")
            self._run = run
        if run.state != "leased":
            self._stop.set()


def _control_call[ResultT](operation: str, call: Callable[[], ResultT]) -> ResultT:
    try:
        return call()
    except ProcedureControlError:
        raise
    except Exception as error:
        raise ProcedureControlError(operation, error) from error


def _heartbeat_delay(lease: ProcedureWorkerLease) -> float:
    remaining = max((lease.expires_at - datetime.now(UTC)).total_seconds(), 0.0)
    return max(min(lease.heartbeat_interval_seconds, remaining / 3), 0.001)


def _exception_reason(error: Exception) -> str:
    detail = str(error).strip()
    return type(error).__name__ if not detail else f"{type(error).__name__}: {detail}"


__all__ = [
    "ProcedureContext",
    "ProcedureControl",
    "ProcedureControlError",
    "ProcedureLeaseLostError",
    "ProcedureNeedsAttention",
    "ProcedureWorker",
]
