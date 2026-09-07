"""Fenced executor application service."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import JsonValue, RootModel
from scopecat.control.models import (
    ControlRun,
    DurableEventInput,
    RunExecutionSegmentPage,
)
from scopecat.control.models import (
    ExecutorLease as ControlExecutorLease,
)
from scopecat.daemon.points import (
    RunDomainDecisionCommand,
    RunDomainDecisionView,
    RunPointPlanCloseCommand,
    RunPointPlanView,
)
from scopecat.daemon.wire import (
    ExecutorHeartbeat,
    ExecutorLease,
    ExecutorStartRequest,
    MeasurementFlushCommand,
    MeasurementFlushReceipt,
    MeasurementHeaderCommand,
    MeasurementIngestReceipt,
    MeasurementSealCommand,
    RunCancellationReceipt,
    RunCancellationState,
    RunCoverageAdvanceCommand,
    RunCoverageState,
    RunDomainJobStatePage,
    RunDomainJobTransitionBatchCommand,
    RunDomainJobTransitionBatchReceipt,
    RunDomainJobTransitionPage,
    RunRecoveryGroupCommitCommand,
    RunRecoveryGroupCommitReceipt,
    RunRecoveryGroupPage,
    TerminalRunCommitCommand,
)
from scopecat.kernel.errors import NotFound
from scopecat.kernel.problems import ProblemPhase, problem
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.content import ModelWrite
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetReceipt,
)
from scopecat.records.run import RunSnapshot
from scopecat.runs.repository import TerminalRunCommit

from scopecat_server.storage.sqlite.control_plane import (
    ControlPlaneConflict,
    ControlPlaneNotFound,
    ExecutorLeaseNotHeld,
    RunResourcesBusy,
    SQLiteControlPlane,
)
from scopecat_server.storage.sqlite.execution import (
    ExecutionStateConflict,
    SQLiteDomainJobTransitions,
    SQLiteMeasurementDatasetRepository,
    SQLiteRecoveryGroups,
    SQLiteRunCoverage,
)
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository

from ..errors import BackendConflict, BackendNotFound
from .active_measurements import ActiveMeasurementConflict, ActiveMeasurementStore
from .point_plans import RunPointPlanService

if TYPE_CHECKING:
    from ..instruments.service import InstrumentService


class _JsonDocument(RootModel[dict[str, JsonValue]]):
    pass


class ExecutorService:
    """Own executor leases and every fenced durable execution write."""

    def __init__(
        self,
        *,
        control: SQLiteControlPlane,
        runs: SQLiteRunRepository,
        instruments: InstrumentService,
        active_measurements: ActiveMeasurementStore,
        point_plans: RunPointPlanService,
        lease_ttl: timedelta | None = None,
    ) -> None:
        self._control = control
        self._runs = runs
        self._instruments = instruments
        self._active_measurements = active_measurements
        self._point_plans = point_plans
        self._lease_ttl = lease_ttl or timedelta(seconds=30)
        self._heartbeat_interval_seconds = self._lease_ttl.total_seconds() / 3
        self._measurement_repositories: dict[
            str, SQLiteMeasurementDatasetRepository
        ] = {}

    def _control_run(self, run_id: str) -> ControlRun:
        try:
            return self._control.get_run(run_id)
        except ControlPlaneNotFound as error:
            raise BackendNotFound(str(error)) from error

    def start_executor(
        self,
        run_id: str,
        request: ExecutorStartRequest,
    ) -> ExecutorLease:
        return self._start_execution(
            run_id,
            executor_id=request.executor_id,
            on_resource_busy=request.on_resource_busy,
        )

    def heartbeat_executor(
        self,
        run_id: str,
        heartbeat: ExecutorHeartbeat,
    ) -> ExecutorLease:
        try:
            renewed = self._control.renew_executor_lease(
                run_id,
                heartbeat.lease_id,
                ttl=self._lease_ttl,
            )
        except ControlPlaneConflict as error:
            raise BackendConflict(str(error)) from error
        current = self._control_run(run_id)
        return self._wire_lease(
            renewed,
            cancellation_requested_at=current.cancellation_requested_at,
        )

    def run_coverage(self, run_id: str) -> RunCoverageState:
        self._control_run(run_id)
        completed = SQLiteRunCoverage(self._runs, run_id=run_id).read()
        return RunCoverageState(
            run_id=run_id,
            completed_point_count=completed,
        )

    def execution_segments(
        self,
        run_id: str,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunExecutionSegmentPage:
        """List immutable executor-ownership intervals for one run."""

        try:
            return self._control.list_run_execution_segments(
                run_id,
                limit=limit,
                before=before,
            )
        except ControlPlaneNotFound as error:
            raise BackendNotFound(str(error)) from error

    def advance_run_coverage(
        self,
        run_id: str,
        command: RunCoverageAdvanceCommand,
    ) -> RunCoverageState:
        coverage = SQLiteRunCoverage(self._runs, run_id=run_id)
        with self.fenced_write(run_id, token=command.lease_id) as connection:
            run = self._control.get_run_in_transaction(connection, run_id)
            end_index = command.start_index + command.point_count
            if end_index > run.admission.plan.point_limit:
                raise ExecutionStateConflict(
                    "coverage range exceeds the admitted point count"
                )
            completed, _advanced = coverage.advance_in_transaction(
                connection,
                start_index=command.start_index,
                point_count=command.point_count,
            )
        return RunCoverageState(
            run_id=run_id,
            completed_point_count=completed,
        )

    def recovery_groups(
        self,
        run_id: str,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunRecoveryGroupPage:
        self._control_run(run_id)
        return SQLiteRecoveryGroups(self._runs, run_id=run_id).read(
            limit=limit,
            before=before,
        )

    def commit_recovery_groups(
        self,
        run_id: str,
        command: RunRecoveryGroupCommitCommand,
    ) -> RunRecoveryGroupCommitReceipt:
        groups = SQLiteRecoveryGroups(self._runs, run_id=run_id)
        with self.fenced_write(run_id, token=command.lease_id) as connection:
            run = self._control.get_run_in_transaction(connection, run_id)
            if any(
                point_index >= run.admission.plan.point_limit
                for group in command.groups
                for point_index in group.point_indices
            ):
                raise ExecutionStateConflict(
                    "recovery group references a point outside the admitted run"
                )
            lease = self._control.executor_lease_for_run_in_transaction(
                connection,
                run_id,
            )
            if lease is None:
                raise ExecutionStateConflict(
                    "recovery group commit requires an executor lease"
                )
            committed = groups.append_in_transaction(
                connection,
                command.groups,
                segment_id=lease.segment_id,
            )
        return RunRecoveryGroupCommitReceipt(run_id=run_id, items=committed)

    def domain_job_transitions(
        self,
        run_id: str,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunDomainJobTransitionPage:
        self._control_run(run_id)
        return SQLiteDomainJobTransitions(self._runs, run_id=run_id).read(
            limit=limit,
            before=before,
        )

    def domain_jobs(
        self,
        run_id: str,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunDomainJobStatePage:
        self._control_run(run_id)
        return SQLiteDomainJobTransitions(self._runs, run_id=run_id).read_current(
            limit=limit,
            before=before,
        )

    def commit_domain_job_transitions(
        self,
        run_id: str,
        command: RunDomainJobTransitionBatchCommand,
    ) -> RunDomainJobTransitionBatchReceipt:
        transitions = SQLiteDomainJobTransitions(self._runs, run_id=run_id)
        with self.fenced_write(run_id, token=command.lease_id) as connection:
            run = self._control.get_run_in_transaction(connection, run_id)
            if any(
                ordinal >= run.admission.plan.point_limit
                for item in command.items
                for ordinal in item.point_ordinals
            ):
                raise ExecutionStateConflict(
                    "domain job transition references a point outside the admitted run"
                )
            committed = transitions.commit_batch_in_transaction(
                connection,
                command,
            )
        return RunDomainJobTransitionBatchReceipt(
            run_id=run_id,
            items=committed,
        )

    def append_run_domain_decision(
        self,
        run_id: str,
        command: RunDomainDecisionCommand,
    ) -> RunDomainDecisionView:
        with self.fenced_write(run_id, token=command.lease_id) as connection:
            return self._point_plans.append_decision_in_transaction(
                connection,
                run_id,
                command,
            )

    def close_run_point_plan(
        self,
        run_id: str,
        command: RunPointPlanCloseCommand,
    ) -> RunPointPlanView:
        coverage = SQLiteRunCoverage(self._runs, run_id=run_id)
        with self.fenced_write(run_id, token=command.lease_id) as connection:
            completed = coverage.read_in_transaction(connection)
            return self._point_plans.close_in_transaction(
                connection,
                run_id,
                command,
                completed_point_count=completed,
            )

    def run_cancellation(self, run_id: str) -> RunCancellationState:
        current = self._control.get_run(run_id)
        return RunCancellationState(
            run_id=run_id,
            requested=current.cancellation_requested_at is not None,
        )

    def cancel_run(self, run_id: str) -> RunCancellationReceipt:
        """Cancel queued work now or request a leased executor checkpoint stop."""

        requested_at = datetime.now(tz=UTC)
        outcome = RunOutcome(
            run_id=run_id,
            result="cancelled",
            certainty="known",
            finished_at=requested_at,
            problems=(
                problem(
                    "run_cancelled_before_execution",
                    "run was cancelled before execution started",
                    phase=ProblemPhase.EXECUTION,
                ),
            ),
        )
        prepared = self._runs.prepare_terminal_commit(
            TerminalRunCommit(run_id=run_id, outcome=outcome)
        )
        try:
            with self._control.write_transaction() as connection:
                current = self._control.get_run_in_transaction(connection, run_id)
                snapshot = self._runs.read_snapshot_in_transaction(connection, run_id)
                if current.state == "closed":
                    return _cancellation_receipt(current, snapshot)
                if current.state == "attention_required":
                    raise ControlPlaneConflict(
                        "attention-required run must be reconciled before it can close"
                    )
                current = self._control.request_run_cancellation_in_transaction(
                    connection,
                    run_id,
                    at=requested_at,
                )
                if current.state == "leased":
                    return RunCancellationReceipt(
                        run_id=run_id,
                        status="cancel_requested",
                        cancellation_requested_at=current.cancellation_requested_at,
                    )
                self._point_plans.abandon_in_transaction(
                    connection,
                    run_id,
                    operation_id="point-plan.terminal.cancelled",
                    reason="run cancelled",
                )
                snapshot = self._runs.commit_prepared_terminal_in_transaction(
                    connection,
                    prepared,
                )
                current = self._control.close_run_in_transaction(
                    connection,
                    run_id,
                    at=requested_at,
                )
                return _cancellation_receipt(current, snapshot)
        except ControlPlaneNotFound as error:
            raise BackendNotFound(str(error)) from error
        except ControlPlaneConflict as error:
            raise BackendConflict(str(error)) from error

    def ingest_measurements(
        self,
        run_id: str,
        *,
        lease_id: str,
        content: bytes,
    ) -> MeasurementIngestReceipt:
        from scopecat.measurements.recording_arrow import (
            MeasurementArrowCodecError,
            decode_measurement_append,
        )

        lease = self.fence_executor(run_id, lease_id)
        try:
            if self._active_measurements.segment_id(run_id) != lease.segment_id:
                raise ActiveMeasurementConflict(
                    "measurement ingest belongs to a different execution segment"
                )
            dataset_schema = self._measurement_repository(run_id).measurement_schema()
            if dataset_schema is None:
                raise ActiveMeasurementConflict(
                    "measurement ingest requires a registered dataset schema"
                )
            append = decode_measurement_append(content, dataset_schema)
            if append.run_id != run_id:
                raise ActiveMeasurementConflict(
                    "measurement ingest run id does not match its route"
                )
            self._active_measurements.ingest(append)
            receipts = self._flush_measurements(
                run_id,
                token=lease_id,
                force=False,
            )
            preview = self._active_measurements.preview(run_id)
        except (ActiveMeasurementConflict, MeasurementArrowCodecError) as error:
            raise BackendConflict(str(error)) from error
        return MeasurementIngestReceipt(
            run_id=run_id,
            received_record_count=preview.received_record_count,
            durable_record_count=preview.durable_record_count,
            durable_receipts=receipts,
        )

    def flush_measurements(
        self,
        run_id: str,
        command: MeasurementFlushCommand,
    ) -> MeasurementFlushReceipt:
        lease = self.fence_executor(run_id, command.lease_id)
        try:
            if self._active_measurements.segment_id(run_id) != lease.segment_id:
                raise ActiveMeasurementConflict(
                    "measurement flush belongs to a different execution segment"
                )
            receipts = self._flush_measurements(
                run_id,
                token=command.lease_id,
                force=True,
            )
            durable_record_count = self._active_measurements.durable_record_count(
                run_id
            )
        except ActiveMeasurementConflict as error:
            raise BackendConflict(str(error)) from error
        return MeasurementFlushReceipt(
            run_id=run_id,
            durable_record_count=durable_record_count,
            durable_receipts=receipts,
        )

    def initialize_measurements(
        self,
        run_id: str,
        command: MeasurementHeaderCommand,
    ) -> MeasurementDatasetReceipt:
        repository = self._measurement_repository(run_id)
        try:
            prepared = repository.prepare_header(command.header)
        except ExecutionStateConflict as error:
            raise BackendConflict(
                "measurement command conflicts with durable state"
            ) from error
        with self.fenced_write(
            run_id,
            token=command.lease_id,
        ) as connection:
            run = self._control.get_run_in_transaction(connection, run_id)
            if (
                command.header.expected_record_count != run.admission.plan.point_count
                or command.header.record_count_limit != run.admission.plan.point_limit
            ):
                raise ExecutionStateConflict(
                    "measurement point extent differs from the admitted run plan"
                )
            lease = self._control.executor_lease_for_run_in_transaction(
                connection,
                run_id,
            )
            if lease is None or lease.token != command.lease_id:
                raise ExecutorLeaseNotHeld(
                    "executor lease is absent, stale, or expired"
                )
            receipt, header_created, fragment_created = (
                repository.header_prepared_in_transaction(
                    connection,
                    prepared,
                    segment_id=lease.segment_id,
                )
            )
            if header_created:
                self.append_effect_event_in_transaction(
                    connection,
                    run_id,
                    "measurement_dataset_initialized",
                    command.header.operation_id,
                )
            if fragment_created:
                self.append_effect_event_in_transaction(
                    connection,
                    run_id,
                    "measurement_fragment_initialized",
                    lease.segment_id,
                )
        fragment = next(
            fragment
            for fragment in repository.measurement_fragments()
            if fragment.segment_id == lease.segment_id
        )
        try:
            self._active_measurements.initialize(
                command.header,
                segment_id=lease.segment_id,
                acquisition_start=fragment.acquisition_start,
            )
        except ActiveMeasurementConflict as error:
            raise BackendConflict(str(error)) from error
        return receipt

    def seal_measurements(
        self,
        run_id: str,
        command: MeasurementSealCommand,
    ) -> MeasurementDatasetReceipt:
        active = self._active_measurements.preview(run_id)
        if active.received_record_count != active.durable_record_count:
            raise BackendConflict(
                "measurement dataset must flush all received records before sealing"
            )
        repository = self._measurement_repository(run_id)
        try:
            segment_id = self._active_measurements.segment_id(run_id)
        except ActiveMeasurementConflict as error:
            raise BackendConflict(str(error)) from error
        try:
            prepared = repository.prepare_seal(command.seal)
        except ExecutionStateConflict as error:
            raise BackendConflict(
                "measurement command conflicts with durable state"
            ) from error
        with self.fenced_write(
            run_id,
            token=command.lease_id,
        ) as connection:
            receipt, created = repository.seal_prepared_in_transaction(
                connection,
                prepared,
                segment_id=segment_id,
            )
            if created:
                self.append_effect_event_in_transaction(
                    connection,
                    run_id,
                    "measurements_sealed",
                    command.seal.operation_id,
                )
        self._measurement_repositories.pop(run_id, None)
        self._active_measurements.clear(run_id)
        return receipt

    def _flush_measurements(
        self,
        run_id: str,
        *,
        token: str,
        force: bool,
    ) -> tuple[MeasurementDatasetReceipt, ...]:
        repository = self._measurement_repository(run_id)
        segment_id = self._active_measurements.segment_id(run_id)
        receipts: list[MeasurementDatasetReceipt] = []
        while records := self._active_measurements.next_chunk(run_id, force=force):
            append = MeasurementDatasetAppend(
                run_id=run_id,
                header_content_hash=self._active_measurements.header_content_hash(
                    run_id
                ),
                acquisition_start=self._active_measurements.durable_record_count(
                    run_id
                ),
                records=records,
            )
            try:
                prepared = repository.prepare_append(append)
            except ExecutionStateConflict as error:
                raise BackendConflict(
                    "measurement command conflicts with durable state"
                ) from error
            with self.fenced_write(run_id, token=token) as connection:
                receipt, created = repository.append_prepared_in_transaction(
                    connection,
                    prepared,
                    segment_id=segment_id,
                )
                if created:
                    self.append_effect_event_in_transaction(
                        connection,
                        run_id,
                        "measurements_appended",
                        append.operation_id,
                    )
            self._active_measurements.commit_chunk(run_id, records)
            receipts.append(receipt)
        return tuple(receipts)

    def retain_measurements_before_fence(self, run_id: str, token: str) -> None:
        """Persist received output while the failing executor still owns its lease."""
        if not self._active_measurements.preview(run_id).active:
            return
        self._flush_measurements(run_id, token=token, force=True)

    def _measurement_repository(
        self,
        run_id: str,
    ) -> SQLiteMeasurementDatasetRepository:
        repository = self._measurement_repositories.get(run_id)
        if repository is None:
            repository = SQLiteMeasurementDatasetRepository(
                self._runs,
                run_id=run_id,
            )
            self._measurement_repositories[run_id] = repository
        return repository

    def commit_terminal(
        self,
        run_id: str,
        command: TerminalRunCommitCommand,
    ) -> RunSnapshot:
        commit = TerminalRunCommit(
            run_id=run_id,
            outcome=command.outcome,
            contents=command.contents,
            models=tuple(
                ModelWrite(
                    ref=write.ref,
                    value=_JsonDocument(root=write.value),
                )
                for write in command.models
            ),
        )
        control_run = self._control_run(run_id)
        commit = _honor_cancellation(control_run, commit)
        if control_run.state == "closed":
            snapshot = self._runs.read_snapshot(run_id)
            if not _matches_terminal_intent(snapshot, commit, self._runs):
                raise BackendConflict("run already has a different terminal outcome")
            self._instruments.release_run(run_id)
            self._measurement_repositories.pop(run_id, None)
            self._active_measurements.clear(run_id)
            return snapshot
        if commit.outcome.result == "succeeded":
            with self._runs.sqlite.read_transaction() as connection:
                self._validate_successful_point_progress_in_transaction(
                    connection,
                    control_run,
                )
        self._instruments.finalize_run(
            run_id,
            token=command.lease_id,
        )
        try:
            snapshot = self.commit_terminal_with_authority(
                run_id,
                token=command.lease_id,
                commit=commit,
            )
        except BackendConflict:
            current = self._control_run(run_id)
            snapshot = self._runs.read_snapshot(run_id)
            if current.state != "closed" or not _matches_terminal_intent(
                snapshot,
                commit,
                self._runs,
            ):
                raise
        self._instruments.release_run(run_id)
        self._measurement_repositories.pop(run_id, None)
        self._active_measurements.clear(run_id)
        return snapshot

    def reconcile_volatile_state(self) -> None:
        """Release measurement state whose executor can no longer write."""

        for run_id in self._active_measurements.run_ids():
            try:
                run = self._control.get_run(run_id)
            except ControlPlaneNotFound:
                self._discard_measurement_state(run_id)
                continue
            if run.state != "leased":
                self._discard_measurement_state(run_id)

    def close(self) -> None:
        """Release all process-local executor state during daemon shutdown."""

        self._measurement_repositories.clear()
        self._active_measurements.clear_all()

    def _discard_measurement_state(self, run_id: str) -> None:
        self._measurement_repositories.pop(run_id, None)
        self._active_measurements.clear(run_id)

    def _start_execution(
        self,
        run_id: str,
        *,
        executor_id: str,
        on_resource_busy: Literal["keep_queued", "fail"],
    ) -> ExecutorLease:
        try:
            with self._control.write_transaction() as connection:
                current = self._control.get_run_in_transaction(connection, run_id)
                latest_snapshot = self._runs.read_snapshot_in_transaction(
                    connection,
                    run_id,
                )
                if current.state == "leased":
                    lease = self._control.executor_lease_for_run_in_transaction(
                        connection,
                        run_id,
                    )
                    if (
                        lease is None
                        or lease.expires_at <= datetime.now(tz=UTC)
                        or lease.executor_id != executor_id
                        or latest_snapshot.outcome is not None
                    ):
                        raise ControlPlaneConflict(
                            "run is already owned by a different executor intent"
                        )
                    return self._wire_lease(
                        lease,
                        cancellation_requested_at=current.cancellation_requested_at,
                    )
                if latest_snapshot.outcome is not None:
                    raise ControlPlaneConflict(
                        "run snapshot is not ready to start execution"
                    )
                try:
                    lease = self._control.start_execution_in_transaction(
                        connection,
                        run_id,
                        executor_id=executor_id,
                        ttl=self._lease_ttl,
                    )
                except RunResourcesBusy:
                    if on_resource_busy == "keep_queued":
                        raise
                    self._fail_resource_rejection(connection, run_id)
                else:
                    return self._wire_lease(
                        lease,
                        cancellation_requested_at=current.cancellation_requested_at,
                    )
            # Report rejection only after committing the terminal state. Raising
            # inside the transaction would roll it back and leave an orphan queue.
            raise BackendConflict("run resources are busy")
        except ControlPlaneNotFound as error:
            raise BackendNotFound(str(error)) from error
        except ControlPlaneConflict as error:
            raise BackendConflict(str(error)) from error

    def _fail_resource_rejection(
        self, connection: sqlite3.Connection, run_id: str
    ) -> None:
        finished_at = datetime.now(tz=UTC)
        outcome = RunOutcome(
            run_id=run_id,
            result="failed",
            certainty="known",
            finished_at=finished_at,
            problems=(
                problem(
                    "run_resources_busy",
                    "run resources are busy; execution did not start",
                    phase=ProblemPhase.EXECUTION,
                ),
            ),
        )
        prepared = self._runs.prepare_terminal_commit(
            TerminalRunCommit(run_id=run_id, outcome=outcome)
        )
        self._point_plans.abandon_in_transaction(
            connection,
            run_id,
            operation_id="point-plan.terminal.resource-rejection",
            reason="run resources are busy",
        )
        self._runs.commit_prepared_terminal_in_transaction(connection, prepared)
        self._control.reject_queued_run_in_transaction(
            connection, run_id, at=finished_at
        )

    def fence_executor(self, run_id: str, token: str) -> ControlExecutorLease:
        try:
            return self._control.validate_executor_lease(
                run_id,
                token=token,
            )
        except ExecutorLeaseNotHeld as error:
            raise BackendConflict(
                "executor lease is absent, stale, or expired"
            ) from error

    @contextmanager
    def fenced_write(
        self,
        run_id: str,
        *,
        token: str,
    ) -> Generator[sqlite3.Connection]:
        try:
            with self._control.fenced_transaction(
                run_id,
                token=token,
            ) as connection:
                yield connection
        except (
            ControlPlaneConflict,
            ExecutionStateConflict,
        ) as error:
            raise BackendConflict(
                "executor command conflicts with durable run state"
            ) from error

    def _wire_lease(
        self,
        lease: ControlExecutorLease,
        *,
        cancellation_requested_at: datetime | None,
    ) -> ExecutorLease:
        return ExecutorLease(
            lease_id=lease.token,
            segment_id=lease.segment_id,
            run_id=lease.run_id,
            executor_id=lease.executor_id,
            issued_at=lease.acquired_at,
            expires_at=lease.expires_at,
            heartbeat_interval_seconds=self._heartbeat_interval_seconds,
            cancellation_requested_at=cancellation_requested_at,
        )

    def append_effect_event_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        kind: str,
        operation_id: str,
    ) -> None:
        self._control.append_event_in_transaction(
            connection,
            DurableEventInput(
                run_id=run_id,
                kind=kind,
                payload={"operation_id": operation_id},
            ),
        )

    def commit_terminal_with_authority(
        self,
        run_id: str,
        *,
        token: str,
        commit: TerminalRunCommit,
    ) -> RunSnapshot:
        prepared = self._runs.prepare_terminal_commit(commit)
        with self.fenced_write(
            run_id,
            token=token,
        ) as connection:
            control = self._control.get_run_in_transaction(connection, run_id)
            commit = _honor_cancellation(control, commit)
            if commit.outcome.result == "succeeded":
                self._validate_successful_point_progress_in_transaction(
                    connection,
                    control,
                )
            else:
                self._point_plans.abandon_in_transaction(
                    connection,
                    run_id,
                    operation_id=f"point-plan.terminal.{commit.outcome.result}",
                    reason=f"run {commit.outcome.result}",
                )
            prepared = replace(prepared, commit=commit)
            snapshot = self._runs.commit_prepared_terminal_in_transaction(
                connection,
                prepared,
            )
            self._control.finish_execution_segment_in_transaction(
                connection,
                run_id,
                token=token,
                result=commit.outcome.result,
                certainty=commit.outcome.certainty,
                at=commit.outcome.finished_at,
            )
            self._control.close_run_in_transaction(
                connection,
                run_id,
                executor_token=token,
            )
            return snapshot

    def _validate_successful_point_progress_in_transaction(
        self,
        connection: sqlite3.Connection,
        control: ControlRun,
    ) -> None:
        run_id = control.run_id
        completed = SQLiteRunCoverage(
            self._runs,
            run_id=run_id,
        ).read_in_transaction(connection)
        admitted = control.admission.plan
        if completed < admitted.initial_point_count or completed > admitted.point_limit:
            raise BackendConflict(
                "successful run coverage does not match its admitted point extent"
            )
        if admitted.point_count is not None:
            if completed != admitted.point_count:
                raise BackendConflict(
                    "successful run coverage does not match its admitted point extent"
                )
            return
        point_plan = self._point_plans.read_in_transaction(connection, run_id)
        if not point_plan.plan_closed:
            raise BackendConflict(
                "successful adaptive run requires a closed durable point plan"
            )
        if completed != point_plan.accepted_point_count:
            raise BackendConflict(
                "successful adaptive run coverage does not match its accepted prefix"
            )


def _matches_terminal_intent(
    current: RunSnapshot,
    commit: TerminalRunCommit,
    runs: SQLiteRunRepository,
) -> bool:
    if current.run_id != commit.run_id or current.outcome != commit.outcome:
        return False
    for expected in commit.contents:
        try:
            actual = runs.read_content(
                commit.run_id,
                role=expected.role,
                content_id=expected.id,
            )
        except NotFound:
            return False
        if actual != expected:
            return False
    return True


def _cancellation_receipt(
    control: ControlRun,
    snapshot: RunSnapshot,
) -> RunCancellationReceipt:
    outcome = snapshot.outcome
    if outcome is None:
        raise AssertionError("closed cancellation receipt requires a terminal outcome")
    requested_at = control.cancellation_requested_at
    status = (
        "cancelled"
        if requested_at is not None and outcome.result == "cancelled"
        else "not_accepted"
    )
    return RunCancellationReceipt(
        run_id=control.run_id,
        status=status,
        cancellation_requested_at=requested_at,
        outcome=outcome,
    )


def _honor_cancellation(
    control: ControlRun,
    commit: TerminalRunCommit,
) -> TerminalRunCommit:
    requested_at = control.cancellation_requested_at
    if requested_at is None or commit.outcome.result != "succeeded":
        return commit
    return replace(
        commit,
        outcome=RunOutcome(
            run_id=commit.run_id,
            result="cancelled",
            certainty="known",
            finished_at=commit.outcome.finished_at,
            problems=(
                problem(
                    "run_cancellation_requested",
                    "run cancellation won the terminal commit race",
                    phase=ProblemPhase.EXECUTION,
                ),
            ),
        ),
    )
