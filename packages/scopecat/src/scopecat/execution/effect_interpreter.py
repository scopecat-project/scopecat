"""Synchronous coverage orchestration for a provisioned ``RunProgram``."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import scopecat.execution.effect_result as effect_result
from scopecat.execution.effects.boundary import EffectBoundary
from scopecat.execution.effects.compute import ComputeEffectExecutor, PointEffectState
from scopecat.execution.effects.domain import (
    DomainResidencyCache,
    execute_domain_job_values,
)
from scopecat.execution.effects.hardware import HardwareEffectExecutor
from scopecat.execution.local.program import (
    ApplyStateOperation,
    CollectOperation,
    ComputeOperation,
    InvokeOperation,
)
from scopecat.execution.program import (
    RunCoverageCheckpoint,
    RunCoverageEffect,
    RunCoveredOperation,
    RunDomainJob,
    RunHostParameterEvidence,
)
from scopecat.execution.services import RunDomainJobTransitionWriter
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.points import AcceptedRunPoint
from scopecat.kernel.problems import Problem
from scopecat.measurements.records import ValueRecordCandidate
from scopecat.records.execution import (
    InstrumentFinalizationActionEvidence,
    InstrumentStateActionEvidence,
    InstrumentStateActionEvidenceLog,
)
from scopecat.records.instrument import InstrumentStateSnapshot
from scopecat.records.parameter_read import HostParameterEvidence
from scopecat.sdk.domain.evidence import DomainExecutionEvidence
from scopecat.sdk.domain.execution import DomainTransitionPolicy
from scopecat.sdk.domain.runtime import (
    DomainExecutionCancellationRequested,
    DomainExecutionId,
    DomainExecutionReceipt,
    DomainJobCheckpoint,
)
from scopecat.sdk.instruments.execution import (
    RunHardwareApply,
    RunHardwareBatch,
    RunHardwareBatchReceipt,
    RunHardwareFinalizationReceipt,
    RunInstrumentHost,
)
from scopecat.sdk.payloads import EMPTY_PAYLOAD_CODECS, PayloadCodecRegistry


class _CapturedDomainEffectFailure(Exception):
    """Stop the operation loop after retaining a structured domain failure."""


class _CapturedCoverageFailure(Exception):
    """Stop after retaining a failure from the coverage consumer."""


class _CancellationRequested(Exception):
    """Stop at an interpreter checkpoint after recording the durable reason."""


_STATE_ACTION_EVIDENCE_RETENTION_LIMIT = 4096


@dataclass(slots=True)
class _StateActionEvidenceBuilder:
    """Count every confirmed state action while retaining a bounded prefix."""

    retention_limit: int = _STATE_ACTION_EVIDENCE_RETENTION_LIMIT
    total_count: int = 0
    retained_prefix: list[InstrumentStateActionEvidence] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.retention_limit < 1:
            raise ValueError("state action evidence retention limit must be positive")

    def observe(self, actions: Sequence[InstrumentStateActionEvidence]) -> None:
        self.total_count += len(actions)
        remaining = self.retention_limit - len(self.retained_prefix)
        if remaining > 0:
            self.retained_prefix.extend(actions[:remaining])

    def build(self) -> InstrumentStateActionEvidenceLog | None:
        if self.total_count == 0:
            return None
        return InstrumentStateActionEvidenceLog(
            total_count=self.total_count,
            retained_prefix=tuple(self.retained_prefix),
            detail_complete=self.total_count == len(self.retained_prefix),
        )


class _StateActionCapturingInstrumentHost:
    """Observe state receipts from ordinary and nested domain hardware batches."""

    def __init__(
        self,
        instruments: RunInstrumentHost,
        evidence: _StateActionEvidenceBuilder,
    ) -> None:
        self._instruments = instruments
        self._evidence = evidence

    @property
    def ready(self) -> bool:
        return self._instruments.ready

    @property
    def setup_problems(self) -> tuple[Problem, ...]:
        return self._instruments.setup_problems

    @property
    def observed_state(self) -> tuple[InstrumentStateSnapshot, ...]:
        return self._instruments.observed_state

    @property
    def baseline_state(self) -> tuple[InstrumentStateSnapshot, ...]:
        return self._instruments.baseline_state

    def execute(self, batch: RunHardwareBatch) -> RunHardwareBatchReceipt:
        receipt = self._instruments.execute(batch)
        if receipt.operation_id == batch.operation_id:
            self._evidence.observe(_validated_state_actions(batch, receipt))
        return receipt

    def finish(
        self,
        *,
        operation_id: str,
        failed: bool,
    ) -> RunHardwareFinalizationReceipt:
        return self._instruments.finish(operation_id=operation_id, failed=failed)


def _validated_state_actions(
    batch: RunHardwareBatch,
    receipt: RunHardwareBatchReceipt,
) -> tuple[InstrumentStateActionEvidence, ...]:
    """Correlate daemon state evidence with the submitted apply prefix."""

    expected = tuple(
        action for action in batch.actions if isinstance(action, RunHardwareApply)
    )
    actual = receipt.state_actions
    if len(actual) > len(expected):
        raise ValueError("hardware batch returned unexpected state action evidence")
    if (
        not receipt.problems
        and not receipt.indeterminate
        and len(actual) != len(expected)
    ):
        raise ValueError("successful hardware batch omitted state action evidence")
    for action, evidence in zip(expected, actual, strict=False):
        if (
            evidence.operation_id != action.effect_id
            or evidence.instrument_id != action.instrument_id
            or evidence.point_index != action.point_index
        ):
            raise ValueError(
                "hardware state action evidence does not match its request"
            )
        requested = tuple(
            (assignment.target, assignment.value) for assignment in action.assignments
        )
        if any(
            (assignment.target, assignment.value) not in requested
            for assignment in evidence.assignments
        ):
            raise ValueError(
                "hardware state action evidence changed its requested value"
            )
    return actual


class _CancellationAwareDomainInstruments:
    """Fence each nested domain batch with the run cancellation signal."""

    def __init__(
        self,
        instruments: RunInstrumentHost,
        cancellation_requested: Callable[[], bool],
    ) -> None:
        self._instruments = instruments
        self._cancellation_requested = cancellation_requested

    def execute(self, batch: RunHardwareBatch) -> RunHardwareBatchReceipt:
        if self._cancellation_requested():
            raise DomainExecutionCancellationRequested
        return self._instruments.execute(batch)


def _never_cancel() -> bool:
    return False


@dataclass(slots=True)
class _DomainExecutionEvidenceBuilder:
    attempt_count: int = 0
    checkpoint_count: int = 0
    receipt_count: int = 0
    completed_count: int = 0
    not_executed_count: int = 0
    unknown_count: int = 0
    detail_complete: bool = True
    target_ids: set[str] = field(default_factory=set)
    transition_policies: set[DomainTransitionPolicy] = field(default_factory=set)

    def observe(
        self,
        *,
        target_id: str,
        transition_policy: DomainTransitionPolicy,
        checkpoints: tuple[DomainJobCheckpoint, ...],
        receipt: DomainExecutionReceipt | None,
    ) -> None:
        self.attempt_count += 1
        self.checkpoint_count += len(checkpoints)
        self.target_ids.add(target_id)
        self.transition_policies.add(transition_policy)
        if receipt is None:
            return
        self.receipt_count += 1
        match receipt.status:
            case "completed":
                self.completed_count += 1
            case "not_executed":
                self.not_executed_count += 1
            case "unknown":
                self.unknown_count += 1

    def build(self, *, run_id: str) -> DomainExecutionEvidence | None:
        if self.attempt_count == 0:
            return None
        return DomainExecutionEvidence(
            run_id=run_id,
            detail_complete=self.detail_complete,
            attempt_count=self.attempt_count,
            checkpoint_count=self.checkpoint_count,
            receipt_count=self.receipt_count,
            completed_count=self.completed_count,
            not_executed_count=self.not_executed_count,
            unknown_count=self.unknown_count,
            target_ids=tuple(sorted(self.target_ids)),
            transition_policies=tuple(sorted(self.transition_policies)),
        )


class RunEffectInterpreter:
    """Execute provisioned host operations in exact program order.

    The caller must create the durable run skeleton before invoking ``run``.
    Terminal certainty and run outcome are derived by the outer run boundary
    from the facts returned here.
    """

    def __init__(
        self,
        *,
        run_id: str,
        coordinate_ids: Sequence[str],
        instruments: RunInstrumentHost,
        coverage_observer: effect_result.CoverageMeasurementObserver | None = None,
        recorded_value_ids: Sequence[ValueId] = (),
        payload_codecs: PayloadCodecRegistry = EMPTY_PAYLOAD_CODECS,
        cancellation_requested: Callable[[], bool] = _never_cancel,
        domain_job_transitions: RunDomainJobTransitionWriter | None = None,
        publish_host_parameter_evidence: Callable[[HostParameterEvidence], None]
        | None = None,
        completed_point_count: int = 0,
        completed_point_indices: Sequence[int] = (),
    ) -> None:
        self.run_id = run_id
        self.coordinate_ids = frozenset(coordinate_ids)
        self.run_points: Sequence[AcceptedRunPoint] = ()
        self.observed_state = list(instruments.observed_state)
        self.baseline_state = list(instruments.baseline_state)
        self.final_state: list[InstrumentStateSnapshot] = []
        self.finalization_actions: list[InstrumentFinalizationActionEvidence] = []
        self._state_actions = _StateActionEvidenceBuilder()
        instrument_host = _StateActionCapturingInstrumentHost(
            instruments,
            self._state_actions,
        )
        self._domain_execution = _DomainExecutionEvidenceBuilder()
        self.domain_failure: tuple[RunDomainJob, BaseException] | None = None
        self.coverage_failure: BaseException | None = None
        self.cancelled = False
        self._point_states: dict[int, PointEffectState] = {}
        self._active_point_indices: set[int] = set()
        self._terminal_point_indices: set[int] = set()
        self._initial_completed_point_count = completed_point_count
        self._initial_completed_point_indices = tuple(completed_point_indices)

        self._boundary = EffectBoundary(run_id=run_id)
        self._compute = ComputeEffectExecutor(
            boundary=self._boundary,
            payload_codecs=payload_codecs,
        )
        self._hardware = HardwareEffectExecutor(
            instruments=instrument_host,
            problems=self._boundary,
        )
        self._coverage_observer = coverage_observer
        self._recorded_value_ids = tuple(recorded_value_ids)
        self._instruments = instrument_host
        self._cancellation_requested = cancellation_requested
        self._domain_job_transitions = domain_job_transitions
        self._publish_host_parameter_evidence = publish_host_parameter_evidence
        self._recorded_domain_invocations: set[str] = set()
        self._unrecorded_completed_domain_attempts: dict[
            str,
            tuple[DomainExecutionId, DomainExecutionReceipt],
        ] = {}
        self._domain_residency = DomainResidencyCache()
        self._domain_instruments = _CancellationAwareDomainInstruments(
            instrument_host,
            cancellation_requested,
        )

    def run(
        self,
        coverage: Iterable[RunCoveredOperation],
        *,
        points: Sequence[AcceptedRunPoint],
        success_state: Sequence[ApplyStateOperation] = (),
        success_state_parameter_evidence: HostParameterEvidence | None = None,
    ) -> effect_result.RunEffectResult:
        """Interpret the residual effect sequence exactly in program order."""

        try:
            self._check_cancellation()
            if not self._boundary.problems:
                self.run_points = points
                if self._initial_completed_point_count > len(self.run_points):
                    raise ValueError(
                        "completed coverage exceeds the interpreter point domain"
                    )
                if any(
                    point_index < 0 or point_index >= len(self.run_points)
                    for point_index in self._initial_completed_point_indices
                ):
                    raise ValueError(
                        "completed recovery group exceeds the interpreter point domain"
                    )
                self._terminal_point_indices.update(
                    range(self._initial_completed_point_count)
                )
                self._terminal_point_indices.update(
                    self._initial_completed_point_indices
                )
                self._execute_coverage_operations(coverage)
            if (
                not bool(self._boundary.problems)
                and self.domain_failure is None
                and len(self._terminal_point_indices) != len(self.run_points)
            ):
                raise AssertionError("run ended without completing every logical point")
            if (
                not bool(self._boundary.problems)
                and not self._boundary.indeterminate
                and self._boundary.interruption is None
                and self.domain_failure is None
                and self.coverage_failure is None
            ):
                self._check_cancellation()
                if success_state_parameter_evidence is not None:
                    self._execute_covered_operation(
                        RunHostParameterEvidence(success_state_parameter_evidence)
                    )
                    self._check_cancellation()
                if self._hardware.execute_success_state(success_state):
                    self._check_cancellation()
        except CheckFailed as error:
            self._boundary.problems.extend(error.problems)
        except _CapturedDomainEffectFailure:
            pass
        except _CapturedCoverageFailure:
            pass
        except _CancellationRequested:
            pass
        except Exception as error:
            self._boundary.problems.append(
                self._boundary.problem_from_exception(
                    "run_effect_interpretation_failed",
                    "run effect interpretation failed",
                    error,
                )
            )
        except BaseException as error:
            self._boundary.record_interruption(error)
        finally:
            if self._active_point_indices:
                self._complete_coverage(
                    tuple(sorted(self._active_point_indices)),
                )
            self._flush_domain_job_transitions()
            try:
                finished = self._instruments.finish(
                    operation_id="hardware.finish",
                    failed=(
                        bool(self._boundary.problems)
                        or self._boundary.indeterminate
                        or self._boundary.interruption is not None
                        or self.domain_failure is not None
                        or self.coverage_failure is not None
                    ),
                )
                self.final_state = list(finished.final_state)
                self.finalization_actions = list(finished.actions)
                self._boundary.problems.extend(finished.problems)
                self._boundary.indeterminate = (
                    self._boundary.indeterminate or finished.indeterminate
                )
            except Exception as error:
                self._boundary.indeterminate = True
                self._boundary.problems.append(
                    self._boundary.problem_from_exception(
                        "hardware_finalization_unknown",
                        "daemon hardware finalization outcome is unknown",
                        error,
                        operation_id="hardware.finish",
                    )
                )
        return self._result()

    def _execute_coverage_operations(
        self,
        operations: Iterable[RunCoveredOperation],
    ) -> None:
        hardware: list[RunCoverageEffect] = []
        for operation in operations:
            self._check_cancellation()
            if isinstance(operation, RunCoverageEffect) and isinstance(
                operation.operation,
                ApplyStateOperation | InvokeOperation | CollectOperation,
            ):
                self._point_state(operation.point_index)
                hardware.append(operation)
                continue
            if hardware:
                self._check_cancellation()
                if not self._execute_hardware_block(hardware):
                    return
                hardware.clear()
                self._check_cancellation()
            if isinstance(operation, RunCoverageCheckpoint):
                self._commit_coverage_checkpoint(operation)
                continue
            self._execute_covered_operation(operation)
            if bool(self._boundary.problems):
                return
            self._check_cancellation()
        if hardware:
            self._check_cancellation()
            if not self._execute_hardware_block(hardware):
                return
            self._check_cancellation()
        remaining = tuple(
            point_index
            for point_index in range(len(self.run_points))
            if point_index not in self._terminal_point_indices
        )
        if remaining:
            self._commit_coverage(remaining)
            self._check_cancellation()

    def _execute_hardware_block(
        self,
        hardware: Sequence[RunCoverageEffect],
    ) -> bool:
        succeeded = self._hardware.execute(
            hardware,
            frame_for=self._point_state,
        )
        if succeeded:
            # Host writes can destroy opaque target-owned program memory. A
            # reconciled property value is not proof that such memory survived.
            # Keep residency on unrelated instruments; domain-owned requirements
            # remain part of the target's own setup contract.
            self._domain_residency.invalidate_instruments(
                {
                    effect.operation.instrument_id
                    for effect in hardware
                    if isinstance(
                        effect.operation,
                        ApplyStateOperation | InvokeOperation | CollectOperation,
                    )
                }
            )
        return succeeded

    def _commit_coverage_checkpoint(
        self,
        checkpoint: RunCoverageCheckpoint,
    ) -> None:
        if any(not self._known_point(index) for index in checkpoint.point_indices):
            raise AssertionError("coverage checkpoint references an unknown point")
        self._commit_coverage(
            checkpoint.point_indices,
            group_id=checkpoint.group_id,
        )

    def _commit_coverage(
        self,
        point_indices: tuple[int, ...],
        *,
        group_id: str | None = None,
    ) -> None:
        value_candidates = tuple(
            ValueRecordCandidate(
                logical_point_id=self._point(point_index).logical_id,
                value_id=value_id,
                value=state.compute_results[value_id],
            )
            for point_index in point_indices
            if (state := self._point_states.get(point_index)) is not None
            for value_id in self._recorded_value_ids
            if value_id in state.compute_results
        )
        self._complete_coverage(point_indices)
        try:
            points = tuple(self._point(point_index) for point_index in point_indices)
            if self._coverage_observer is not None:
                selected = frozenset(point_indices)
                candidates = tuple(
                    candidate
                    for candidate in self._hardware.values
                    if candidate.logical_point_id.logical_ordinal in selected
                )
                self._coverage_observer(
                    group_id,
                    points,
                    candidates,
                    value_candidates,
                )
                self._hardware.values[:] = (
                    candidate
                    for candidate in self._hardware.values
                    if candidate.logical_point_id.logical_ordinal not in selected
                )
        except BaseException as error:
            self.coverage_failure = error
            raise _CapturedCoverageFailure from error

    def _execute_covered_operation(self, operation: RunCoveredOperation) -> None:
        match operation:
            case RunHostParameterEvidence():
                if self._publish_host_parameter_evidence is None:
                    raise RuntimeError("host preparation requires an evidence writer")
                self._publish_host_parameter_evidence(operation.evidence)
            case RunCoverageCheckpoint():
                raise AssertionError("coverage checkpoint bypassed coverage sequencing")
            case RunDomainJob():
                self._execute_domain_job(operation)
            case RunCoverageEffect():
                self._execute_coverage_effect(operation)

    def _execute_coverage_effect(self, covered: RunCoverageEffect) -> None:
        if not isinstance(covered.operation, ComputeOperation):
            raise AssertionError("hardware effect bypassed batch execution")
        self._compute.execute(
            self._point_state(covered.point_index),
            (covered.operation,),
        )

    def _execute_domain_job(self, job: RunDomainJob) -> None:
        if self._domain_job_transitions is None:
            raise RuntimeError("domain job execution requires a transition ledger")
        for point_index in job.point_ordinals:
            if point_index in self._terminal_point_indices:
                raise AssertionError("domain job follows point completion")
            if not self._known_point(point_index):
                raise AssertionError("domain job references an unknown point")
            self._active_point_indices.add(point_index)
        try:
            execute_domain_job_values(
                job.execution,
                logical_compute_node_id=job.id,
                run_id=self.run_id,
                instruments=self._domain_instruments,
                accept=self._hardware.values.append,
                observe_attempt=lambda execution_id, checkpoints, receipt: (
                    self._record_domain_attempt(
                        job,
                        execution_id,
                        checkpoints,
                        receipt,
                    )
                ),
                commit_invocation=lambda execution_id: (
                    self._commit_domain_job_invocation(job, execution_id)
                ),
                commit_checkpoint=lambda execution_id, checkpoint: (
                    self._commit_domain_job_checkpoint(
                        job,
                        execution_id,
                        checkpoint,
                    )
                ),
                commit_terminal=lambda execution_id, receipt: (
                    self._commit_domain_job_terminal(job, execution_id, receipt)
                ),
                residency=self._domain_residency,
            )
            self._unrecorded_completed_domain_attempts.pop(job.id, None)
        except DomainExecutionCancellationRequested:
            self._check_cancellation()
            raise AssertionError(
                "domain cancellation marker requires a live request"
            ) from None
        except BaseException as error:
            self._record_failed_domain_realization(job)
            self.domain_failure = (job, error)
            pending = tuple(sorted(self._active_point_indices))
            if pending:
                self._complete_coverage(pending)
            raise _CapturedDomainEffectFailure(job.id) from error

    def _commit_domain_job_invocation(
        self,
        job: RunDomainJob,
        execution_id: DomainExecutionId,
    ) -> None:
        if job.execution.transition_policy != "abnormal_only":
            try:
                self._record_domain_job_invocation(
                    job,
                    execution_id,
                    write_ahead=job.execution.transition_policy == "write_ahead",
                )
            except Exception:
                self._domain_execution.detail_complete = False
                raise
        if self._cancellation_requested():
            raise DomainExecutionCancellationRequested

    def _commit_domain_job_checkpoint(
        self,
        job: RunDomainJob,
        execution_id: DomainExecutionId,
        checkpoint: DomainJobCheckpoint,
    ) -> None:
        writer = self._domain_job_transitions
        if writer is not None:
            try:
                if execution_id.execution_key not in self._recorded_domain_invocations:
                    self._record_domain_job_invocation(
                        job,
                        execution_id,
                        write_ahead=False,
                    )
                writer.checkpoint(
                    logical_compute_node_id=job.id,
                    point_ordinals=job.point_ordinals,
                    checkpoint=checkpoint,
                )
            except Exception:
                self._domain_execution.detail_complete = False
                raise
        if self._cancellation_requested():
            raise DomainExecutionCancellationRequested

    def _commit_domain_job_terminal(
        self,
        job: RunDomainJob,
        execution_id: DomainExecutionId,
        receipt: DomainExecutionReceipt,
    ) -> None:
        policy = job.execution.transition_policy
        invocation_recorded = (
            execution_id.execution_key in self._recorded_domain_invocations
        )
        if (
            policy == "abnormal_only"
            and receipt.status == "completed"
            and not invocation_recorded
        ):
            return
        writer = self._domain_job_transitions
        if writer is not None:
            try:
                if not invocation_recorded:
                    self._record_domain_job_invocation(
                        job,
                        execution_id,
                        write_ahead=False,
                    )
                writer.terminal(
                    logical_compute_node_id=job.id,
                    point_ordinals=job.point_ordinals,
                    receipt=receipt,
                    write_ahead=(policy == "write_ahead" or policy == "abnormal_only"),
                )
            except Exception:
                self._domain_execution.detail_complete = False
                raise

    def _flush_domain_job_transitions(self) -> None:
        writer = self._domain_job_transitions
        if writer is None:
            return
        try:
            writer.flush()
        except Exception as error:
            self._domain_execution.detail_complete = False
            self._boundary.indeterminate = True
            self._boundary.problems.append(
                self._boundary.problem_from_exception(
                    "domain_job_transition_flush_unknown",
                    "buffered domain job transitions could not be flushed",
                    error,
                    operation_id="domain.transitions.flush",
                )
            )

    def _record_domain_attempt(
        self,
        job: RunDomainJob,
        execution_id: DomainExecutionId,
        checkpoints: tuple[DomainJobCheckpoint, ...],
        receipt: DomainExecutionReceipt | None,
    ) -> None:
        self._domain_execution.observe(
            target_id=job.execution.invocation.intent.target_id,
            transition_policy=job.execution.transition_policy,
            checkpoints=checkpoints,
            receipt=receipt,
        )
        if (
            job.execution.transition_policy == "abnormal_only"
            and receipt is None
            and execution_id.execution_key not in self._recorded_domain_invocations
        ):
            try:
                self._record_domain_job_invocation(
                    job,
                    execution_id,
                    write_ahead=True,
                )
            except Exception:
                self._domain_execution.detail_complete = False
        if (
            job.execution.transition_policy == "abnormal_only"
            and receipt is not None
            and receipt.status == "completed"
            and execution_id.execution_key not in self._recorded_domain_invocations
        ):
            self._unrecorded_completed_domain_attempts[job.id] = (
                execution_id,
                receipt,
            )
        if receipt is not None:
            self._recorded_domain_invocations.discard(execution_id.execution_key)

    def _record_failed_domain_realization(self, job: RunDomainJob) -> None:
        deferred = self._unrecorded_completed_domain_attempts.pop(job.id, None)
        if deferred is None:
            return
        execution_id, receipt = deferred
        try:
            self._record_domain_job_invocation(
                job,
                execution_id,
                write_ahead=False,
            )
            writer = self._domain_job_transitions
            if writer is not None:
                writer.terminal(
                    logical_compute_node_id=job.id,
                    point_ordinals=job.point_ordinals,
                    receipt=receipt,
                    write_ahead=True,
                )
        except Exception:
            self._domain_execution.detail_complete = False

    def _record_domain_job_invocation(
        self,
        job: RunDomainJob,
        execution_id: DomainExecutionId,
        *,
        write_ahead: bool,
    ) -> None:
        writer = self._domain_job_transitions
        if writer is None:
            return
        writer.invocation(
            logical_compute_node_id=job.id,
            point_ordinals=job.point_ordinals,
            execution_id=execution_id,
            intent=job.execution.invocation.intent,
            write_ahead=write_ahead,
        )
        self._recorded_domain_invocations.add(execution_id.execution_key)

    def _point_state(self, point_index: int) -> PointEffectState:
        if point_index in self._terminal_point_indices:
            raise AssertionError("point effect follows point completion")
        state = self._point_states.get(point_index)
        if state is not None:
            return state
        logical_id = self._point(point_index).logical_id
        state = PointEffectState(
            point_index=point_index,
            logical_id=logical_id,
        )
        self._point_states[point_index] = state
        self._active_point_indices.add(point_index)
        return state

    def _complete_coverage(
        self,
        point_indices: tuple[int, ...],
    ) -> None:
        if not point_indices or len(point_indices) != len(set(point_indices)):
            raise AssertionError("point coverage must be non-empty and unique")
        if any(
            point_index in self._terminal_point_indices for point_index in point_indices
        ):
            raise AssertionError("point coverage overlaps completed points")
        if any(not self._known_point(point_index) for point_index in point_indices):
            raise AssertionError("point coverage references an unknown point")
        for point_index in point_indices:
            self._point_states.pop(point_index, None)
            self._active_point_indices.discard(point_index)
        self._terminal_point_indices.update(point_indices)

    def _known_point(self, point_index: int) -> bool:
        return 0 <= point_index < len(self.run_points)

    def _point(self, point_index: int) -> AcceptedRunPoint:
        if not self._known_point(point_index):
            raise AssertionError("point effect references an unknown point")
        point = self.run_points[point_index]
        if point.ordinal != point_index:
            raise ValueError("run points must retain canonical contiguous ordinals")
        if frozenset(point.coordinates) != self.coordinate_ids:
            raise ValueError("run point coordinates do not match the run contract")
        return point

    def _check_cancellation(self) -> None:
        if self.cancelled or not self._cancellation_requested():
            return
        self.cancelled = True
        self._boundary.problems.append(
            self._boundary.problem(
                "run_cancellation_requested",
                "run stopped at a safe checkpoint after cancellation was requested",
            )
        )
        raise _CancellationRequested

    def _result(self) -> effect_result.RunEffectResult:
        return effect_result.RunEffectResult(
            problems=tuple(self._boundary.problems),
            observed_state=tuple(self.observed_state),
            baseline_state=tuple(self.baseline_state),
            final_state=tuple(self.final_state),
            state_actions=self._state_actions.build(),
            finalization_actions=tuple(self.finalization_actions),
            domain_execution=self._domain_execution.build(run_id=self.run_id),
            indeterminate=self._boundary.indeterminate,
            cancelled=self.cancelled,
            domain_failure=self.domain_failure,
            coverage_failure=self.coverage_failure,
            interruption=self._boundary.interruption,
        )


__all__ = ["RunEffectInterpreter"]
