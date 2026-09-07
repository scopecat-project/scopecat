"""Exception boundaries for expected Scopecat operation failures.

``ProblemFailure`` turns one or more structured findings into Python control
flow at an operation boundary.
Programming errors and violated internal invariants are not normalized into
expected problems. External-effect uncertainty retains its execution context
rather than pretending to be an ordinary validation failure or safely retryable
work.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from scopecat.kernel.problems import Problem
from scopecat.kernel.run_outcome import (
    RunCertainty,
    RunOutcome,
)


class ScopecatError(Exception):
    """Base error for Scopecat failures."""


class SessionClosedError(ScopecatError):
    """A lazy operation needs the daemon connection that has already closed."""


class ProblemFailure(ScopecatError):
    """Base for expected failures described by structured problems."""

    def __init__(self, problems: Sequence[Problem]) -> None:
        selected = tuple(problems)
        if not selected:
            msg = "problem failure requires at least one problem"
            raise ValueError(msg)
        self.problems = selected
        summary = "; ".join(
            f"{problem.code}: {problem.message}" for problem in selected
        )
        super().__init__(summary)


class CheckFailed(ProblemFailure):
    """A definition, authoring, configuration, or planning check failed."""


class OperationFailure(ProblemFailure):
    """An expected non-check operation failed."""


class NotFound(OperationFailure):
    """A requested domain object does not exist."""


class Conflict(OperationFailure):
    """The requested operation conflicts with current state."""


class DataIntegrityError(OperationFailure):
    """Persisted or external data violates its integrity contract."""


class StorageError(OperationFailure):
    """Storage could not complete the requested operation."""


class ProviderContractError(OperationFailure):
    """A provider violates or cannot satisfy its declared contract."""


class ComputeExecutionError(OperationFailure):
    """A point-local compute could not execute its declared contract."""


class RunFinalizationFailed(ProblemFailure):
    """Execution evidence exists locally, but terminal persistence is unconfirmed.

    Inspect ``execution_outcome`` and query the saved run before deciding what to
    do next. This exception never grants permission to repeat a hardware effect.
    """

    terminal_persistence: Literal["unconfirmed"] = "unconfirmed"

    def __init__(
        self, *, execution_outcome: RunOutcome, finalization_problems: Sequence[Problem]
    ) -> None:
        self.run_id = execution_outcome.run_id
        self.execution_outcome = execution_outcome
        self.finalization_problems = tuple(finalization_problems)
        super().__init__((*execution_outcome.problems, *self.finalization_problems))


class RunFailure(ProblemFailure):
    """Base for terminal run failures with a durable outcome."""

    def __init__(self, *, run_id: str, outcome: RunOutcome) -> None:
        if outcome.run_id != run_id:
            msg = "run failure outcome does not belong to the selected run"
            raise ValueError(msg)
        self.run_id = run_id
        self.outcome = outcome
        super().__init__(outcome.problems)


class RunFailed(RunFailure):
    """A run reached a known failed terminal outcome."""

    def __init__(self, *, run_id: str, outcome: RunOutcome) -> None:
        if outcome.result != "failed" or outcome.certainty != "known":
            msg = "RunFailed requires a known failed outcome"
            raise ValueError(msg)
        super().__init__(run_id=run_id, outcome=outcome)


class RunCancelled(RunFailure):
    """A run honored an operator cancellation at a known safe boundary."""

    def __init__(self, *, run_id: str, outcome: RunOutcome) -> None:
        if outcome.result != "cancelled" or outcome.certainty != "known":
            msg = "RunCancelled requires a known cancelled outcome"
            raise ValueError(msg)
        super().__init__(run_id=run_id, outcome=outcome)


class RunIndeterminate(RunFailure):
    """A run terminated without establishing the outcome of every effect."""

    def __init__(self, *, run_id: str, outcome: RunOutcome) -> None:
        if outcome.certainty != "indeterminate":
            msg = "RunIndeterminate requires an indeterminate outcome"
            raise ValueError(msg)
        super().__init__(run_id=run_id, outcome=outcome)


class MeasurementDatasetReceiptEvidence(Protocol):
    """Runtime-resolvable boundary for one durable dataset receipt."""

    @property
    def operation_id(self) -> str: ...

    @property
    def dataset_content_hash(self) -> str: ...

    @property
    def dataset_ref(self) -> str: ...


class DomainExecutionReceiptEvidence(Protocol):
    """Receipt identity retained without coupling kernel errors to domain SDK."""

    @property
    def execution_key(self) -> str: ...


class MeasurementRecordingError(StorageError):
    """A projected record write or its ledger evidence did not complete cleanly."""

    def __init__(
        self,
        problems: Sequence[Problem],
        *,
        run_id: str,
        dataset_id: str,
        recording_contract_fingerprint: str,
        operation_id: str,
        receipt: MeasurementDatasetReceiptEvidence | None,
        write_may_have_completed: bool,
    ) -> None:
        if (
            not run_id
            or not dataset_id
            or not recording_contract_fingerprint
            or not operation_id
        ):
            msg = "measurement recording identity fields must be non-empty"
            raise ValueError(msg)
        if receipt is not None and not receipt.operation_id:
            msg = "measurement dataset receipt operation id must be non-empty"
            raise ValueError(msg)
        self.run_id = run_id
        self.dataset_id = dataset_id
        self.recording_contract_fingerprint = recording_contract_fingerprint
        self.operation_id = operation_id
        self.receipt = receipt
        self.write_may_have_completed = write_may_have_completed
        super().__init__(problems)


class DomainExecutionFailed(OperationFailure):
    """One domain job failed or reached an indeterminate outcome."""

    def __init__(
        self,
        problems: Sequence[Problem],
        *,
        run_id: str,
        operation_id: str,
        invocation_id: str,
        execution_key: str,
        certainty: RunCertainty,
        receipt: DomainExecutionReceiptEvidence | None,
    ) -> None:
        if not run_id or not operation_id or not invocation_id or not execution_key:
            msg = "domain execution failure identity fields must be non-empty"
            raise ValueError(msg)
        self.run_id = run_id
        self.operation_id = operation_id
        self.invocation_id = invocation_id
        self.execution_key = execution_key
        self.phase: Literal["execute"] = "execute"
        self.certainty: RunCertainty = certainty
        self.receipt = receipt
        super().__init__(problems)
