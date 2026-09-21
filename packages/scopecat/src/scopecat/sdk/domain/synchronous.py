"""Compose one synchronous hardware batch into a correlated domain result."""

from collections.abc import Callable

from scopecat.kernel.content_identity import content_fingerprint, sha256_json_hash
from scopecat.kernel.problems import ProblemPhase, problem
from scopecat.sdk.domain.runtime import (
    DomainExecutionReceipt,
    DomainExecutionResult,
    DomainInstrumentExecutor,
)
from scopecat.sdk.instruments.execution import RunHardwareBatch, RunHardwareValue


def execute_domain_batch[ResultT](
    execution_key: str,
    batch: RunHardwareBatch,
    *,
    instruments: DomainInstrumentExecutor,
    artifact_fingerprint: str,
    result_count: int,
    decode_result: Callable[[tuple[RunHardwareValue, ...]], ResultT],
) -> DomainExecutionResult[ResultT] | DomainExecutionReceipt:
    """Execute once using the host channel and close terminal result evidence.

    Use inside ``DomainJobRuntime.start`` after host write-ahead admission.
    Decoding validates device-specific content correlation and shapes; units and
    normalization remain adapter policy. Results must support structural content
    fingerprinting. Exceptions propagate to the existing domain failure boundary;
    this function neither retries nor owns reservations, cleanup or cancellation.
    """

    receipt = instruments.execute(batch)
    if receipt.operation_id != batch.operation_id:
        raise ValueError("hardware receipt belongs to another batch")
    expected = tuple(action.effect_id for action in batch.actions)
    completed = receipt.completed_effect_ids
    if completed != expected[: len(completed)]:
        raise ValueError("hardware receipt must acknowledge an ordered action prefix")
    if receipt.problems or receipt.indeterminate:
        problems = receipt.problems or (
            problem(
                "hardware_batch_indeterminate",
                "hardware batch did not establish a complete execution outcome",
                phase=ProblemPhase.EXECUTION,
            ),
        )
        return DomainExecutionReceipt(
            execution_key=execution_key,
            status="unknown" if receipt.indeterminate or completed else "not_executed",
            problems=problems,
            execution_evidence={
                "artifact_fingerprint": artifact_fingerprint,
                "completed_effect_ids": list(completed),
            },
        )
    if completed != expected:
        raise ValueError("successful hardware receipt must cover every action")
    result = decode_result(receipt.values)
    return DomainExecutionResult(
        receipt=DomainExecutionReceipt(
            execution_key=execution_key,
            status="completed",
            result_count=result_count,
            result_fingerprint=sha256_json_hash(content_fingerprint(result)),
            execution_evidence={"artifact_fingerprint": artifact_fingerprint},
        ),
        result=result,
    )
