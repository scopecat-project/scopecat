"""Ports bundled for one execution environment."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from scopecat.adaptive_domains import DomainProposalAttempt, OperatorDomainRequest
from scopecat.execution.ports.measurement import MeasurementDatasetWriter
from scopecat.kernel.points import AcceptedRunPoint
from scopecat.optimization import DomainProposalDecision
from scopecat.records.execution import (
    DomainExecutionId,
    DomainExecutionReceipt,
    DomainJobCheckpoint,
    RecoveryGroupCompletion,
)
from scopecat.records.run import RunSnapshot
from scopecat.runs.repository import TerminalRunCommit
from scopecat.sdk.domain.invocation import DomainInvocationIntent
from scopecat.sdk.instruments.execution import RunInstrumentHost


def _never_cancel() -> bool:
    return False


def _effects_are_ready() -> bool:
    return True


def _zero_completed_point_count() -> int:
    return 0


def _no_prior_execution_segment() -> bool:
    return False


def _no_completed_recovery_groups() -> tuple[RecoveryGroupCompletion, ...]:
    return ()


class RunCoverageWriter(Protocol):
    """Commit bounded contiguous logical-point progress."""

    def advance(
        self,
        *,
        start_index: int,
        point_count: int,
        groups: tuple[RecoveryGroupCompletion, ...] = (),
    ) -> None: ...

    def flush(self) -> None: ...


class RunRecoveryGroupWriter(Protocol):
    """Publish exact groups only after their output proof is durable."""

    def commit(self, groups: tuple[RecoveryGroupCompletion, ...]) -> None: ...


class RunDomainJobTransitionWriter(Protocol):
    """Stage correlated target transitions and flush bounded durable batches."""

    def invocation(
        self,
        *,
        logical_compute_node_id: str,
        point_ordinals: tuple[int, ...],
        execution_id: DomainExecutionId,
        intent: DomainInvocationIntent,
        write_ahead: bool,
    ) -> None: ...

    def checkpoint(
        self,
        *,
        logical_compute_node_id: str,
        point_ordinals: tuple[int, ...],
        checkpoint: DomainJobCheckpoint,
    ) -> None: ...

    def terminal(
        self,
        *,
        logical_compute_node_id: str,
        point_ordinals: tuple[int, ...],
        receipt: DomainExecutionReceipt,
        write_ahead: bool,
    ) -> None: ...

    def flush(self) -> None: ...


@dataclass(frozen=True, slots=True)
class QueuedOperatorDomainRequest:
    request: OperatorDomainRequest


class RunDomainProposalWriter(Protocol):
    """Commit point-plan facts and durable proposal decisions."""

    def next_queued(self) -> QueuedOperatorDomainRequest | None: ...

    def append(
        self,
        proposal: DomainProposalAttempt,
        decision: DomainProposalDecision,
        accepted_points: tuple[AcceptedRunPoint, ...],
        *,
        operator_request_id: str | None = None,
    ) -> None: ...

    def close(self, *, completed_point_count: int, reason: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ExecutionSession:
    """Bind one run's effect ports so execution cannot mix storage scopes."""

    accepted: RunSnapshot
    begin: Callable[[], None]
    commit_terminal: Callable[[TerminalRunCommit], RunSnapshot]
    measurements: MeasurementDatasetWriter
    instruments: RunInstrumentHost
    domain_job_transitions: RunDomainJobTransitionWriter | None = None
    coverage: RunCoverageWriter | None = None
    recovery_groups: RunRecoveryGroupWriter | None = None
    domain_proposals: RunDomainProposalWriter | None = None
    cancellation_requested: Callable[[], bool] = _never_cancel
    effects_ready: Callable[[], bool] = _effects_are_ready
    durable_completed_point_count: Callable[[], int] = _zero_completed_point_count
    durable_recovery_groups: Callable[[], tuple[RecoveryGroupCompletion, ...]] = (
        _no_completed_recovery_groups
    )
    has_prior_execution_segment: Callable[[], bool] = _no_prior_execution_segment

    @property
    def run_id(self) -> str:
        return self.accepted.run_id
