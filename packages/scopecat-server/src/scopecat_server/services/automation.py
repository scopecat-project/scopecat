"""Application service for durable multi-run procedure coordination."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from scopecat.analysis.facts import validate_analysis_fact_json
from scopecat.automation import (
    InterpretationOutputRef,
    InterpretationRequest,
    InterpretationResponse,
    ProcedureCancelCommand,
    ProcedureCancellation,
    ProcedureCancelReceipt,
    ProcedureCloseCommand,
    ProcedureCloseReceipt,
    ProcedureCloseStatus,
    ProcedureClosure,
    ProcedureDefinitionRef,
    ProcedureIntent,
    ProcedureRun,
    ProcedureRunAttentionCommand,
    ProcedureRunAttentionReceipt,
    ProcedureRunListQuery,
    ProcedureRunnablePage,
    ProcedureRunnableQuery,
    ProcedureRunPage,
    ProcedureRunState,
    ProcedureStepAttempt,
    ProcedureStepAttemptListQuery,
    ProcedureStepAttemptPage,
    ProcedureStepAttemptState,
    ProcedureStepAttentionCommand,
    ProcedureStepAttentionReceipt,
    ProcedureStepAttentionRetryCommand,
    ProcedureStepAttentionRetryReceipt,
    ProcedureStepBeginCommand,
    ProcedureStepBeginReceipt,
    ProcedureStepCompleteCommand,
    ProcedureStepCompleteReceipt,
    ProcedureStepFailCommand,
    ProcedureStepFailReceipt,
    ProcedureStepInputSubmitCommand,
    ProcedureStepInputSubmitReceipt,
    ProcedureStepInputWaitCommand,
    ProcedureStepInputWaitReceipt,
    ProcedureStepOperation,
    ProcedureStepOutputRef,
    ProcedureSubmitCommand,
    ProcedureSubmitReceipt,
    ProcedureWorkerLease,
    ProcedureWorkerLeaseAcquireCommand,
    ProcedureWorkerLeaseAcquireReceipt,
    ProcedureWorkerLeaseHeartbeatCommand,
    ProcedureWorkerLeaseHeartbeatReceipt,
    ProcedureWorkerLeaseReleaseCommand,
    ProcedureWorkerLeaseReleaseReceipt,
    procedure_intent_hash,
    procedure_step_operation_id,
)
from scopecat.records.content import Sha256ContentHash
from scopecat.records.sample import SampleSelector

from scopecat_server.storage.sqlite.automation import (
    AutomationConflict,
    AutomationNotFound,
    ProcedureLeaseRecord,
    SQLiteAutomationStore,
)
from scopecat_server.storage.sqlite.automation import (
    ProcedureRunPage as StoredProcedureRunPage,
)
from scopecat_server.storage.sqlite.automation import (
    ProcedureStepAttemptPage as StoredProcedureStepAttemptPage,
)

from ..errors import BackendConflict, BackendNotFound

_DEFAULT_PROCEDURE_LEASE_TTL = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class ProcedureStepTransition:
    """Atomic procedure and step snapshots returned by a step command."""

    run: ProcedureRun
    attempt: ProcedureStepAttempt


class AutomationService:
    """Own procedure admission, revision CAS, and worker fencing."""

    def __init__(
        self,
        store: SQLiteAutomationStore,
        *,
        lease_ttl: timedelta = _DEFAULT_PROCEDURE_LEASE_TTL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_ttl <= timedelta(0):
            raise ValueError("procedure lease TTL must be positive")
        self._store = store
        self._lease_ttl = lease_ttl
        self._clock = clock or _utc_now

    def submit(self, command: ProcedureSubmitCommand) -> ProcedureSubmitReceipt:
        return ProcedureSubmitReceipt(
            run=self._submit(
                definition=command.definition,
                request_key=command.request_key,
                intent=command.intent,
                samples=command.samples,
            )
        )

    def list(self, query: ProcedureRunListQuery) -> ProcedureRunPage:
        page = self._list(
            limit=query.limit,
            before=query.cursor,
            state=query.state,
        )
        return ProcedureRunPage(items=page.items, next_cursor=page.next_cursor)

    def runnable(self, query: ProcedureRunnableQuery) -> ProcedureRunnablePage:
        with _translate_store_errors():
            page = self._store.list_runnable(
                query.definitions,
                at=self._now(),
                limit=query.limit,
            )
        return ProcedureRunnablePage(items=page.items, has_more=page.has_more)

    def step_attempts(
        self,
        procedure_run_id: str,
        query: ProcedureStepAttemptListQuery,
    ) -> ProcedureStepAttemptPage:
        page = self._step_attempts(
            procedure_run_id,
            limit=query.limit,
            before=query.cursor,
        )
        return ProcedureStepAttemptPage(
            procedure_run_id=procedure_run_id,
            items=page.items,
            next_cursor=page.next_cursor,
        )

    def acquire_lease(
        self,
        command: ProcedureWorkerLeaseAcquireCommand,
    ) -> ProcedureWorkerLeaseAcquireReceipt:
        lease = self._acquire_lease(
            command.procedure_run_id,
            worker_id=command.worker_id,
            expected_revision=command.expected_run_revision,
        )
        return ProcedureWorkerLeaseAcquireReceipt(
            run=self.get(command.procedure_run_id),
            lease=self._wire_lease(lease),
        )

    def heartbeat_lease(
        self,
        command: ProcedureWorkerLeaseHeartbeatCommand,
    ) -> ProcedureWorkerLeaseHeartbeatReceipt:
        lease = self._renew_lease(
            command.procedure_run_id,
            token=command.lease_token,
        )
        return ProcedureWorkerLeaseHeartbeatReceipt(
            run=self.get(command.procedure_run_id),
            lease=self._wire_lease(lease),
        )

    def release_lease(
        self,
        command: ProcedureWorkerLeaseReleaseCommand,
    ) -> ProcedureWorkerLeaseReleaseReceipt:
        return ProcedureWorkerLeaseReleaseReceipt(
            run=self._release_lease(
                command.procedure_run_id,
                token=command.lease_token,
                expected_revision=command.expected_run_revision,
            )
        )

    def begin_step(
        self,
        command: ProcedureStepBeginCommand,
    ) -> ProcedureStepBeginReceipt:
        transition = self._begin_step(
            command.procedure_run_id,
            token=command.lease_token,
            expected_run_revision=command.expected_run_revision,
            step_key=command.step_key,
            operation=command.operation,
            intent_hash=command.intent_hash,
            inputs=command.inputs,
        )
        return ProcedureStepBeginReceipt(
            run=transition.run,
            step=transition.attempt,
            operation_id=procedure_step_operation_id(
                transition.attempt.procedure_run_id,
                transition.attempt.step_key,
            ),
        )

    def complete_step(
        self,
        command: ProcedureStepCompleteCommand,
    ) -> ProcedureStepCompleteReceipt:
        transition = self._complete_step(
            command.procedure_run_id,
            token=command.lease_token,
            expected_run_revision=command.expected_run_revision,
            step_key=command.step_key,
            attempt=command.attempt,
            expected_attempt_revision=command.expected_step_revision,
            output=command.output,
        )
        return ProcedureStepCompleteReceipt(
            run=transition.run,
            step=transition.attempt,
        )

    def fail_step(
        self,
        command: ProcedureStepFailCommand,
    ) -> ProcedureStepFailReceipt:
        transition = self._fail_step(
            command.procedure_run_id,
            token=command.lease_token,
            expected_run_revision=command.expected_run_revision,
            step_key=command.step_key,
            attempt=command.attempt,
            expected_attempt_revision=command.expected_step_revision,
            reason=command.reason,
        )
        return ProcedureStepFailReceipt(
            run=transition.run,
            step=transition.attempt,
        )

    def wait_step_input(
        self,
        command: ProcedureStepInputWaitCommand,
    ) -> ProcedureStepInputWaitReceipt:
        transition = self._wait_step_input(
            command.procedure_run_id,
            token=command.lease_token,
            expected_run_revision=command.expected_run_revision,
            step_key=command.step_key,
            attempt=command.attempt,
            expected_attempt_revision=command.expected_step_revision,
            request=command.request,
        )
        return ProcedureStepInputWaitReceipt(
            run=transition.run,
            step=transition.attempt,
        )

    def submit_step_input(
        self,
        command: ProcedureStepInputSubmitCommand,
    ) -> ProcedureStepInputSubmitReceipt:
        transition = self._submit_step_input(command)
        output = transition.attempt.output
        if not isinstance(output, InterpretationOutputRef):
            raise RuntimeError("submitted interpretation has no durable output")
        return ProcedureStepInputSubmitReceipt(
            run=transition.run,
            step=transition.attempt,
            output=output,
        )

    def require_step_attention(
        self,
        command: ProcedureStepAttentionCommand,
    ) -> ProcedureStepAttentionReceipt:
        transition = self._require_step_attention(
            command.procedure_run_id,
            token=command.lease_token,
            expected_run_revision=command.expected_run_revision,
            step_key=command.step_key,
            attempt=command.attempt,
            expected_attempt_revision=command.expected_step_revision,
            reason=command.reason,
        )
        return ProcedureStepAttentionReceipt(
            run=transition.run,
            step=transition.attempt,
        )

    def require_run_attention(
        self,
        command: ProcedureRunAttentionCommand,
    ) -> ProcedureRunAttentionReceipt:
        return ProcedureRunAttentionReceipt(
            run=self._require_run_attention(
                command.procedure_run_id,
                token=command.lease_token,
                expected_revision=command.expected_run_revision,
                reason=command.reason,
            )
        )

    def retry_step_attention(
        self,
        command: ProcedureStepAttentionRetryCommand,
    ) -> ProcedureStepAttentionRetryReceipt:
        transition = self._retry_step_attention(
            command.procedure_run_id,
            expected_run_revision=command.expected_run_revision,
            step_key=command.step_key,
            attempt=command.attempt,
            expected_attempt_revision=command.expected_step_revision,
        )
        return ProcedureStepAttentionRetryReceipt(
            run=transition.run,
            step=transition.attempt,
        )

    def cancel(self, command: ProcedureCancelCommand) -> ProcedureCancelReceipt:
        """Close idle work or request a stop after the running step settles."""
        with _translate_store_errors(), self._store.write_transaction() as connection:
            run = self._store.read_run_in_transaction(
                connection, command.procedure_run_id
            )
            if run.cancellation is not None:
                if (
                    run.cancellation.actor == command.actor
                    and run.cancellation.reason == command.reason
                ):
                    return ProcedureCancelReceipt(run=run)
                raise AutomationConflict(
                    "procedure already has a different cancellation request"
                )
            if run.state == "closed":
                raise AutomationConflict("procedure is already closed")
            self._require_revision(run, command.expected_run_revision)
            if run.state == "attention_required":
                raise AutomationConflict(
                    "attention-required procedures need inspection"
                )
            now = self._now()
            pending = run.state == "leased"
            updated = _run_state(
                run,
                state=run.state if pending else "closed",
                at=now,
                closure=None
                if pending
                else ProcedureClosure(
                    status="cancelled",
                    closed_at=now,
                    actor=command.actor,
                    reason=command.reason,
                ),
            )
            updated = updated.model_copy(
                update={
                    "cancellation": ProcedureCancellation(
                        actor=command.actor,
                        reason=command.reason,
                        requested_at=now,
                        requested_revision=updated.revision,
                    )
                }
            )
            self._store.replace_run_in_transaction(
                connection, updated, expected_revision=run.revision
            )
            return ProcedureCancelReceipt(run=updated)

    def close(self, command: ProcedureCloseCommand) -> ProcedureCloseReceipt:
        return ProcedureCloseReceipt(
            run=self._close(
                command.procedure_run_id,
                token=command.lease_token,
                expected_revision=command.expected_run_revision,
                status=command.status,
                reason=command.reason,
            )
        )

    def _submit(
        self,
        *,
        definition: ProcedureDefinitionRef,
        request_key: str,
        intent: ProcedureIntent,
        samples: tuple[SampleSelector, ...] = (),
    ) -> ProcedureRun:
        """Admit one idempotent, version-pinned procedure request."""

        if not request_key.strip():
            raise ValueError("procedure request key must be non-empty")
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            return self.submit_in_transaction(
                connection,
                definition=definition,
                request_key=request_key,
                intent=intent,
                samples=samples,
            )

    def submit_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        definition: ProcedureDefinitionRef,
        request_key: str,
        intent: ProcedureIntent,
        samples: tuple[SampleSelector, ...] = (),
        at: datetime | None = None,
        require_new: bool = False,
    ) -> ProcedureRun:
        """Admit a run while participating in a caller-owned SQLite transaction."""

        if not request_key.strip():
            raise ValueError("procedure request key must be non-empty")
        selected_intent = dict(intent)
        intent_hash = procedure_intent_hash(
            definition,
            selected_intent,
            samples=samples,
        )
        existing = self._store.find_run_by_request_in_transaction(
            connection,
            definition.id,
            request_key,
        )
        if existing is not None:
            if existing.intent_hash != intent_hash:
                raise AutomationConflict(
                    "procedure request key already has different intent"
                )
            if require_new:
                raise AutomationConflict(
                    "procedure request key already has a durable run"
                )
            return existing
        now = self._now() if at is None else _require_aware_time(at)
        run = ProcedureRun(
            procedure_run_id=f"procedure-{uuid4().hex}",
            request_key=request_key,
            definition=definition,
            intent=selected_intent,
            intent_hash=intent_hash,
            samples=samples,
            revision=1,
            state="ready",
            created_at=now,
            updated_at=now,
        )
        self._store.insert_run_in_transaction(connection, run)
        return run

    def get(self, procedure_run_id: str) -> ProcedureRun:
        with _translate_store_errors():
            return self._store.read_run(procedure_run_id)

    def _list(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
        state: ProcedureRunState | None = None,
    ) -> StoredProcedureRunPage:
        return self._store.list_runs(limit=limit, before=before, state=state)

    def _step_attempts(
        self,
        procedure_run_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> StoredProcedureStepAttemptPage:
        with _translate_store_errors():
            return self._store.list_step_attempts(
                procedure_run_id,
                limit=limit,
                before=before,
            )

    def _acquire_lease(
        self,
        procedure_run_id: str,
        *,
        worker_id: str,
        expected_revision: int,
    ) -> ProcedureLeaseRecord:
        """Lease a ready run or take over an expired worker lease."""

        if not worker_id.strip():
            raise ValueError("procedure worker id must be non-empty")
        now = self._now()
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            run = self._store.read_run_in_transaction(connection, procedure_run_id)
            current_lease = self._store.read_lease_in_transaction(
                connection,
                procedure_run_id,
            )
            if (
                run.state == "leased"
                and current_lease is not None
                and current_lease.expires_at > now
                and current_lease.worker_id == worker_id
                and run.revision == expected_revision + 1
            ):
                return current_lease
            self._require_revision(run, expected_revision)
            if run.state == "ready":
                pass
            elif run.state == "leased":
                if current_lease is not None and current_lease.expires_at > now:
                    raise AutomationConflict("procedure run already has a live lease")
            else:
                raise AutomationConflict(
                    "procedure lease requires ready or expired leased state, "
                    f"got {run.state}"
                )
            updated = _run_state(run, state="leased", at=now)
            self._store.replace_run_in_transaction(
                connection,
                updated,
                expected_revision=expected_revision,
            )
            lease = ProcedureLeaseRecord(
                procedure_run_id=procedure_run_id,
                worker_id=worker_id,
                token=uuid4().hex,
                acquired_at=now,
                renewed_at=now,
                expires_at=now + self._lease_ttl,
            )
            self._store.put_lease_in_transaction(connection, lease)
            return lease

    def _renew_lease(
        self,
        procedure_run_id: str,
        *,
        token: str,
    ) -> ProcedureLeaseRecord:
        """Renew a live lease without changing procedure business revision."""

        now = self._now()
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            run = self._store.read_run_in_transaction(connection, procedure_run_id)
            if run.state != "leased":
                raise AutomationConflict("only a leased procedure can renew")
            lease = self._require_live_lease(
                connection,
                procedure_run_id,
                token,
                now,
            )
            renewed = ProcedureLeaseRecord(
                procedure_run_id=lease.procedure_run_id,
                worker_id=lease.worker_id,
                token=lease.token,
                acquired_at=lease.acquired_at,
                renewed_at=now,
                expires_at=now + self._lease_ttl,
            )
            self._store.put_lease_in_transaction(connection, renewed)
            return renewed

    def _release_lease(
        self,
        procedure_run_id: str,
        *,
        token: str,
        expected_revision: int,
    ) -> ProcedureRun:
        """Yield replayable procedure state back to the ready queue.

        A crashed worker can leave a running step attempt whose stable operation
        ID is safe for an exact replacement worker to replay. Releasing the lease
        therefore does not require the run to be between step attempts.
        """

        now = self._now()
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            run = self._store.read_run_in_transaction(connection, procedure_run_id)
            lease = self._store.read_lease_in_transaction(
                connection,
                procedure_run_id,
            )
            if run.state == "ready" and lease is None:
                return run
            self._require_revision(run, expected_revision)
            if run.state != "leased":
                raise AutomationConflict("only a leased procedure can be released")
            self._require_live_lease(
                connection,
                procedure_run_id,
                token,
                now,
            )
            updated = _run_state(run, state="ready", at=now)
            self._store.replace_run_in_transaction(
                connection,
                updated,
                expected_revision=expected_revision,
            )
            self._store.delete_lease_in_transaction(
                connection,
                procedure_run_id,
                token=token,
            )
            return updated

    def _begin_step(
        self,
        procedure_run_id: str,
        *,
        token: str,
        expected_run_revision: int,
        step_key: str,
        operation: ProcedureStepOperation,
        intent_hash: Sha256ContentHash,
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
    ) -> ProcedureStepTransition:
        """Begin one stable step or replay its existing durable attempt."""

        now = self._now()
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            run = self._leased_run(
                connection,
                procedure_run_id,
                token=token,
                at=now,
            )
            if run.cancellation is not None:
                raise AutomationConflict(
                    "procedure cancellation requested; no new steps"
                )
            existing = self._store.latest_step_attempt_in_transaction(
                connection,
                procedure_run_id,
                step_key,
            )
            if existing is not None:
                if (
                    existing.operation != operation
                    or existing.intent_hash != intent_hash
                    or existing.inputs != inputs
                ):
                    raise AutomationConflict(
                        "procedure step key already has different intent"
                    )
                if existing.state in {"running", "succeeded"}:
                    return ProcedureStepTransition(run=run, attempt=existing)
                if existing.state == "failed":
                    raise AutomationConflict(
                        "failed procedure step is terminal for this procedure run"
                    )
            self._require_revision(run, expected_run_revision)
            running = self._store.running_step_attempt_in_transaction(
                connection,
                procedure_run_id,
            )
            if running is not None:
                raise AutomationConflict(
                    f"procedure step is already running: {running.step_key}"
                )
            attempt = ProcedureStepAttempt(
                procedure_run_id=procedure_run_id,
                step_key=step_key,
                attempt=1 if existing is None else existing.attempt + 1,
                operation=operation,
                intent_hash=intent_hash,
                inputs=inputs,
                revision=1,
                state="running",
                started_at=now,
                updated_at=now,
            )
            updated_run = _run_state(run, state="leased", at=now)
            self._store.insert_step_attempt_in_transaction(connection, attempt)
            self._store.replace_run_in_transaction(
                connection,
                updated_run,
                expected_revision=expected_run_revision,
            )
            return ProcedureStepTransition(run=updated_run, attempt=attempt)

    def _complete_step(
        self,
        procedure_run_id: str,
        *,
        token: str,
        expected_run_revision: int,
        step_key: str,
        attempt: int,
        expected_attempt_revision: int,
        output: ProcedureStepOutputRef,
    ) -> ProcedureStepTransition:
        """Commit one exact side-effect output while retaining the worker lease."""

        now = self._now()
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            run = self._leased_run(
                connection,
                procedure_run_id,
                token=token,
                at=now,
            )
            current = self._store.read_step_attempt_in_transaction(
                connection,
                procedure_run_id,
                step_key,
                attempt,
            )
            if current.state == "succeeded":
                if current.output != output:
                    raise AutomationConflict(
                        "procedure step completion has different output"
                    )
                return ProcedureStepTransition(run=run, attempt=current)
            self._require_revision(run, expected_run_revision)
            self._require_attempt_revision(current, expected_attempt_revision)
            if current.state != "running":
                raise AutomationConflict("only a running procedure step can complete")
            if current.operation == "interpretation":
                raise AutomationConflict(
                    "interpretation steps complete through typed input submission"
                )
            updated_attempt = _attempt_state(
                current,
                state="succeeded",
                at=now,
                output=output,
            )
            updated_run = _run_state(run, state="leased", at=now)
            self._store.replace_step_attempt_in_transaction(
                connection,
                updated_attempt,
                expected_revision=expected_attempt_revision,
            )
            self._store.replace_run_in_transaction(
                connection,
                updated_run,
                expected_revision=expected_run_revision,
            )
            return ProcedureStepTransition(
                run=updated_run,
                attempt=updated_attempt,
            )

    def _wait_step_input(
        self,
        procedure_run_id: str,
        *,
        token: str,
        expected_run_revision: int,
        step_key: str,
        attempt: int,
        expected_attempt_revision: int,
        request: InterpretationRequest,
    ) -> ProcedureStepTransition:
        """Publish one expected interpretation and release the worker lease."""

        now = self._now()
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            run = self._store.read_run_in_transaction(connection, procedure_run_id)
            current = self._store.read_step_attempt_in_transaction(
                connection,
                procedure_run_id,
                step_key,
                attempt,
            )
            lease = self._store.read_lease_in_transaction(
                connection,
                procedure_run_id,
            )
            if (
                (
                    run.state == "waiting_for_input"
                    or (run.closure is not None and run.closure.status == "cancelled")
                )
                and current.state == "waiting_for_input"
                and current.interpretation_request == request
                and lease is None
            ):
                return ProcedureStepTransition(run=run, attempt=current)
            self._require_revision(run, expected_run_revision)
            self._require_attempt_revision(current, expected_attempt_revision)
            if (
                run.state != "leased"
                or current.state != "running"
                or current.operation != "interpretation"
            ):
                raise AutomationConflict(
                    "procedure input wait requires a leased interpretation step"
                )
            self._require_live_lease(connection, procedure_run_id, token, now)
            updated_attempt = _attempt_state(
                current,
                state="waiting_for_input",
                at=now,
                interpretation_request=request,
            )
            updated_run = _run_state(run, state="waiting_for_input", at=now)
            if run.cancellation is not None:
                updated_run = _run_state(
                    run,
                    state="closed",
                    at=now,
                    closure=ProcedureClosure(
                        status="cancelled",
                        closed_at=now,
                        actor=run.cancellation.actor,
                        reason=run.cancellation.reason,
                    ),
                )
            self._store.replace_step_attempt_in_transaction(
                connection,
                updated_attempt,
                expected_revision=expected_attempt_revision,
            )
            self._store.replace_run_in_transaction(
                connection,
                updated_run,
                expected_revision=expected_run_revision,
            )
            self._store.delete_lease_in_transaction(
                connection,
                procedure_run_id,
                token=token,
            )
            return ProcedureStepTransition(run=updated_run, attempt=updated_attempt)

    def _submit_step_input(
        self,
        command: ProcedureStepInputSubmitCommand,
    ) -> ProcedureStepTransition:
        """Validate and atomically commit one answer to the exact waiting request."""

        now = self._now()
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            run = self._store.read_run_in_transaction(
                connection,
                command.procedure_run_id,
            )
            current = self._store.read_step_attempt_in_transaction(
                connection,
                command.procedure_run_id,
                command.step_key,
                command.attempt,
            )
            lease = self._store.read_lease_in_transaction(
                connection,
                command.procedure_run_id,
            )
            if current.state == "succeeded":
                output = current.output
                if (
                    isinstance(output, InterpretationOutputRef)
                    and output.request_hash == command.request_hash
                    and output.response.actor == command.actor
                    and output.response.actor_kind == command.actor_kind
                    and output.response.value == command.value
                    and output.response.note == command.note
                ):
                    return ProcedureStepTransition(run=run, attempt=current)
                raise AutomationConflict(
                    "procedure interpretation already has a different response"
                )
            self._require_revision(run, command.expected_run_revision)
            self._require_attempt_revision(current, command.expected_step_revision)
            if (
                run.state != "waiting_for_input"
                or current.state != "waiting_for_input"
                or current.operation != "interpretation"
            ):
                raise AutomationConflict(
                    "procedure input submission requires a waiting interpretation"
                )
            latest = self._store.latest_step_attempt_in_transaction(
                connection,
                command.procedure_run_id,
                command.step_key,
            )
            if latest != current:
                raise AutomationConflict(
                    "procedure input submission must target the latest attempt"
                )
            if lease is not None:
                raise AutomationConflict(
                    "waiting procedure input cannot retain a worker lease"
                )
            request = current.interpretation_request
            if request is None or request.request_hash != command.request_hash:
                raise AutomationConflict("procedure interpretation request changed")
            try:
                validate_analysis_fact_json(command.value, request.structure)
            except TypeError as error:
                raise AutomationConflict(
                    "procedure interpretation response does not match its schema"
                ) from error
            response = InterpretationResponse(
                actor=command.actor,
                actor_kind=command.actor_kind,
                value=command.value,
                note=command.note,
                submitted_at=now,
            )
            output = InterpretationOutputRef(
                procedure_run_id=command.procedure_run_id,
                step_key=command.step_key,
                request_hash=request.request_hash,
                response=response,
            )
            updated_attempt = _attempt_state(
                current,
                state="succeeded",
                at=now,
                output=output,
                interpretation_request=request,
            )
            updated_run = _run_state(run, state="ready", at=now)
            self._store.replace_step_attempt_in_transaction(
                connection,
                updated_attempt,
                expected_revision=command.expected_step_revision,
            )
            self._store.replace_run_in_transaction(
                connection,
                updated_run,
                expected_revision=command.expected_run_revision,
            )
            return ProcedureStepTransition(run=updated_run, attempt=updated_attempt)

    def _require_run_attention(
        self,
        procedure_run_id: str,
        *,
        token: str,
        expected_revision: int,
        reason: str,
    ) -> ProcedureRun:
        """Fence a leased run when no exact step can safely own the failure."""

        if not reason.strip():
            raise ValueError("procedure attention reason must be non-empty")
        now = self._now()
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            run = self._store.read_run_in_transaction(connection, procedure_run_id)
            lease = self._store.read_lease_in_transaction(connection, procedure_run_id)
            if (
                run.state == "attention_required"
                and run.attention_reason == reason
                and lease is None
            ):
                return run
            self._require_revision(run, expected_revision)
            if run.state != "leased":
                raise AutomationConflict(
                    "procedure run attention requires a leased run"
                )
            self._require_live_lease(
                connection,
                procedure_run_id,
                token,
                now,
            )
            if (
                self._store.running_step_attempt_in_transaction(
                    connection,
                    procedure_run_id,
                )
                is not None
            ):
                raise AutomationConflict(
                    "running step attention must use the step attention command"
                )
            updated = _run_state(
                run,
                state="attention_required",
                at=now,
                attention_reason=reason,
            )
            self._store.replace_run_in_transaction(
                connection,
                updated,
                expected_revision=expected_revision,
            )
            self._store.delete_lease_in_transaction(
                connection,
                procedure_run_id,
                token=token,
            )
            return updated

    def _fail_step(
        self,
        procedure_run_id: str,
        *,
        token: str,
        expected_run_revision: int,
        step_key: str,
        attempt: int,
        expected_attempt_revision: int,
        reason: str,
    ) -> ProcedureStepTransition:
        """Record a known step failure without inventing an automatic retry."""

        if not reason.strip():
            raise ValueError("procedure step failure reason must be non-empty")
        now = self._now()
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            run = self._leased_run(
                connection,
                procedure_run_id,
                token=token,
                at=now,
            )
            current = self._store.read_step_attempt_in_transaction(
                connection,
                procedure_run_id,
                step_key,
                attempt,
            )
            if current.state == "failed":
                if current.failure_reason != reason:
                    raise AutomationConflict(
                        "procedure step failure has a different reason"
                    )
                return ProcedureStepTransition(run=run, attempt=current)
            self._require_revision(run, expected_run_revision)
            self._require_attempt_revision(current, expected_attempt_revision)
            if current.state != "running":
                raise AutomationConflict("only a running procedure step can fail")
            updated_attempt = _attempt_state(
                current,
                state="failed",
                at=now,
                failure_reason=reason,
            )
            updated_run = _run_state(run, state="leased", at=now)
            self._store.replace_step_attempt_in_transaction(
                connection,
                updated_attempt,
                expected_revision=expected_attempt_revision,
            )
            self._store.replace_run_in_transaction(
                connection,
                updated_run,
                expected_revision=expected_run_revision,
            )
            return ProcedureStepTransition(
                run=updated_run,
                attempt=updated_attempt,
            )

    def _retry_step_attention(
        self,
        procedure_run_id: str,
        *,
        expected_run_revision: int,
        step_key: str,
        attempt: int,
        expected_attempt_revision: int,
    ) -> ProcedureStepTransition:
        """Return one exact quarantined step to retryable procedure work."""

        now = self._now()
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            run = self._store.read_run_in_transaction(connection, procedure_run_id)
            current = self._store.read_step_attempt_in_transaction(
                connection,
                procedure_run_id,
                step_key,
                attempt,
            )
            if (
                run.state == "ready"
                and run.revision == expected_run_revision + 1
                and current.state == "attention_required"
                and current.revision == expected_attempt_revision
            ):
                return ProcedureStepTransition(run=run, attempt=current)
            self._require_revision(run, expected_run_revision)
            self._require_attempt_revision(current, expected_attempt_revision)
            if (
                run.state != "attention_required"
                or current.state != "attention_required"
            ):
                raise AutomationConflict(
                    "procedure step retry requires attention states"
                )
            if run.attention_reason != current.attention_reason:
                raise AutomationConflict(
                    "procedure run and step attention reasons do not match"
                )
            latest = self._store.latest_step_attempt_in_transaction(
                connection,
                procedure_run_id,
                step_key,
            )
            if latest != current:
                raise AutomationConflict(
                    "procedure step retry must target the latest attempt"
                )
            if (
                self._store.read_lease_in_transaction(
                    connection,
                    procedure_run_id,
                )
                is not None
            ):
                raise AutomationConflict(
                    "attention-required procedure cannot retain a worker lease"
                )
            updated_run = _run_state(run, state="ready", at=now)
            self._store.replace_run_in_transaction(
                connection,
                updated_run,
                expected_revision=expected_run_revision,
            )
            return ProcedureStepTransition(run=updated_run, attempt=current)

    def _require_step_attention(
        self,
        procedure_run_id: str,
        *,
        token: str,
        expected_run_revision: int,
        step_key: str,
        attempt: int,
        expected_attempt_revision: int,
        reason: str,
    ) -> ProcedureStepTransition:
        """Fence the worker and atomically quarantine its running step and run."""

        if not reason.strip():
            raise ValueError("procedure attention reason must be non-empty")
        now = self._now()
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            run = self._store.read_run_in_transaction(connection, procedure_run_id)
            current = self._store.read_step_attempt_in_transaction(
                connection,
                procedure_run_id,
                step_key,
                attempt,
            )
            if (
                run.state == "attention_required"
                and run.attention_reason == reason
                and current.state == "attention_required"
                and current.attention_reason == reason
            ):
                return ProcedureStepTransition(run=run, attempt=current)
            self._require_revision(run, expected_run_revision)
            self._require_attempt_revision(current, expected_attempt_revision)
            if run.state != "leased" or current.state != "running":
                raise AutomationConflict(
                    "procedure attention requires a leased run and running step"
                )
            self._require_live_lease(
                connection,
                procedure_run_id,
                token,
                now,
            )
            updated_attempt = _attempt_state(
                current,
                state="attention_required",
                at=now,
                attention_reason=reason,
            )
            updated_run = _run_state(
                run,
                state="attention_required",
                at=now,
                attention_reason=reason,
            )
            self._store.replace_step_attempt_in_transaction(
                connection,
                updated_attempt,
                expected_revision=expected_attempt_revision,
            )
            self._store.replace_run_in_transaction(
                connection,
                updated_run,
                expected_revision=expected_run_revision,
            )
            self._store.delete_lease_in_transaction(
                connection,
                procedure_run_id,
                token=token,
            )
            return ProcedureStepTransition(
                run=updated_run,
                attempt=updated_attempt,
            )

    def _close(
        self,
        procedure_run_id: str,
        *,
        token: str,
        expected_revision: int,
        status: ProcedureCloseStatus,
        reason: str | None = None,
    ) -> ProcedureRun:
        """Close a leased procedure and invalidate its worker lease."""

        now = self._now()
        closure = ProcedureClosure(status=status, closed_at=now, reason=reason)
        with (
            _translate_store_errors(),
            self._store.write_transaction() as connection,
        ):
            run = self._store.read_run_in_transaction(connection, procedure_run_id)
            if run.cancellation is not None and status in {"succeeded", "cancelled"}:
                status = "cancelled"
                reason = run.cancellation.reason
                closure = ProcedureClosure(
                    status=status,
                    closed_at=now,
                    actor=run.cancellation.actor,
                    reason=reason,
                )
            if run.state == "closed":
                if (
                    run.closure is not None
                    and run.closure.status == status
                    and run.closure.reason == reason
                ):
                    return run
                raise AutomationConflict(
                    "procedure run is already closed with a different result"
                )
            self._require_revision(run, expected_revision)
            if run.state != "leased":
                raise AutomationConflict("only a leased procedure can close")
            self._require_live_lease(
                connection,
                procedure_run_id,
                token,
                now,
            )
            if (
                self._store.running_step_attempt_in_transaction(
                    connection,
                    procedure_run_id,
                )
                is not None
            ):
                raise AutomationConflict(
                    "procedure cannot close while a step attempt is running"
                )
            updated = _run_state(
                run,
                state="closed",
                at=now,
                closure=closure,
            )
            self._store.replace_run_in_transaction(
                connection,
                updated,
                expected_revision=expected_revision,
            )
            self._store.delete_lease_in_transaction(
                connection,
                procedure_run_id,
                token=token,
            )
            return updated

    def _leased_run(
        self,
        connection: sqlite3.Connection,
        procedure_run_id: str,
        *,
        token: str,
        at: datetime,
    ) -> ProcedureRun:
        run = self._store.read_run_in_transaction(connection, procedure_run_id)
        if run.state != "leased":
            raise AutomationConflict("procedure worker effects require a leased run")
        self._require_live_lease(connection, procedure_run_id, token, at)
        return run

    def _require_live_lease(
        self,
        connection: sqlite3.Connection,
        procedure_run_id: str,
        token: str,
        at: datetime,
    ) -> ProcedureLeaseRecord:
        lease = self._store.read_lease_in_transaction(connection, procedure_run_id)
        if lease is None or lease.token != token or lease.expires_at <= at:
            raise AutomationConflict("procedure lease is absent, stale, or expired")
        return lease

    @staticmethod
    def _require_revision(run: ProcedureRun, expected_revision: int) -> None:
        if run.revision != expected_revision:
            raise AutomationConflict("procedure run revision changed")

    @staticmethod
    def _require_attempt_revision(
        attempt: ProcedureStepAttempt,
        expected_revision: int,
    ) -> None:
        if attempt.revision != expected_revision:
            raise AutomationConflict("procedure step revision changed")

    def _wire_lease(self, lease: ProcedureLeaseRecord) -> ProcedureWorkerLease:
        return ProcedureWorkerLease(
            procedure_run_id=lease.procedure_run_id,
            worker_id=lease.worker_id,
            lease_token=lease.token,
            issued_at=lease.acquired_at,
            renewed_at=lease.renewed_at,
            expires_at=lease.expires_at,
            heartbeat_interval_seconds=self._lease_ttl.total_seconds() / 3,
        )

    def _now(self) -> datetime:
        return _require_aware_time(self._clock())


def _run_state(
    run: ProcedureRun,
    *,
    state: ProcedureRunState,
    at: datetime,
    attention_reason: str | None = None,
    closure: ProcedureClosure | None = None,
) -> ProcedureRun:
    return ProcedureRun.model_validate(
        {
            **run.model_dump(),
            "revision": run.revision + 1,
            "state": state,
            "updated_at": at,
            "attention_reason": attention_reason,
            "closure": closure,
        }
    )


def _attempt_state(
    attempt: ProcedureStepAttempt,
    *,
    state: ProcedureStepAttemptState,
    at: datetime,
    output: ProcedureStepOutputRef | None = None,
    interpretation_request: InterpretationRequest | None = None,
    failure_reason: str | None = None,
    attention_reason: str | None = None,
) -> ProcedureStepAttempt:
    return ProcedureStepAttempt.model_validate(
        {
            **attempt.model_dump(),
            "revision": attempt.revision + 1,
            "state": state,
            "updated_at": at,
            "finished_at": at if state in {"succeeded", "failed"} else None,
            "output": output,
            "interpretation_request": interpretation_request,
            "failure_reason": failure_reason,
            "attention_reason": attention_reason,
        }
    )


@contextmanager
def _translate_store_errors() -> Generator[None]:
    try:
        yield
    except AutomationNotFound as error:
        raise BackendNotFound(str(error)) from error
    except AutomationConflict as error:
        raise BackendConflict(str(error)) from error


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("procedure clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


__all__ = [
    "AutomationService",
    "ProcedureStepTransition",
]
