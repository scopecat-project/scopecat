from dataclasses import dataclass

import pytest

from scopecat.kernel.content_identity import content_fingerprint, sha256_json_hash
from scopecat.kernel.problems import ProblemPhase, problem
from scopecat.sdk.domain import DomainExecutionResult, execute_domain_batch
from scopecat.sdk.instruments.execution import (
    RunHardwareBatch,
    RunHardwareBatchReceipt,
    RunHardwareInvoke,
    RunHardwareValue,
)


@dataclass
class Executor:
    receipt: RunHardwareBatchReceipt
    calls: int = 0

    def execute(self, batch: RunHardwareBatch) -> RunHardwareBatchReceipt:
        assert batch.operation_id == "batch"
        self.calls += 1
        return self.receipt


def batch() -> RunHardwareBatch:
    return RunHardwareBatch(
        operation_id="batch",
        actions=tuple(
            RunHardwareInvoke(
                effect_id=name,
                instrument_id="source",
                resource_id="source",
                interface_id="test.control/v1",
                operation_id=name,
            )
            for name in ("first", "second")
        ),
    )


@pytest.mark.parametrize("completed", ((), ("first",)))
@pytest.mark.parametrize("indeterminate", (False, True))
def test_failure_never_decodes_or_retries(
    completed: tuple[str, ...], indeterminate: bool
) -> None:
    issue = problem("rejected", "action rejected", phase=ProblemPhase.EXECUTION)
    executor = Executor(
        RunHardwareBatchReceipt(
            operation_id="batch",
            completed_effect_ids=completed,
            problems=(issue,),
            indeterminate=indeterminate,
        )
    )

    def decode(_values: tuple[RunHardwareValue, ...]) -> str:
        raise AssertionError("failed batch must not decode results")

    outcome = execute_domain_batch(
        "execution",
        batch(),
        instruments=executor,
        artifact_fingerprint="artifact",
        result_count=1,
        decode_result=decode,
    )
    assert not isinstance(outcome, DomainExecutionResult)
    assert outcome.status == (
        "unknown" if completed or indeterminate else "not_executed"
    )
    assert outcome.problems == (issue,)
    assert outcome.execution_evidence["completed_effect_ids"] == list(completed)
    assert executor.calls == 1


def test_indeterminate_without_diagnostic_still_produces_negative_evidence() -> None:
    executor = Executor(
        RunHardwareBatchReceipt(operation_id="batch", indeterminate=True)
    )
    outcome = execute_domain_batch(
        "execution",
        batch(),
        instruments=executor,
        artifact_fingerprint="artifact",
        result_count=1,
        decode_result=lambda values: len(values),
    )
    assert not isinstance(outcome, DomainExecutionResult)
    assert outcome.status == "unknown"
    assert outcome.problems[0].code == "hardware_batch_indeterminate"


def test_success_closes_structural_result_evidence() -> None:
    executor = Executor(
        RunHardwareBatchReceipt(
            operation_id="batch", completed_effect_ids=("first", "second")
        )
    )
    outcome = execute_domain_batch(
        "execution",
        batch(),
        instruments=executor,
        artifact_fingerprint="artifact",
        result_count=1,
        decode_result=lambda values: (len(values), "decoded"),
    )
    assert isinstance(outcome, DomainExecutionResult)
    assert outcome.result == (0, "decoded")
    assert outcome.receipt.execution_key == "execution"
    assert outcome.receipt.result_count == 1
    assert outcome.receipt.result_fingerprint == sha256_json_hash(
        content_fingerprint(outcome.result)
    )
    assert outcome.receipt.execution_evidence == {"artifact_fingerprint": "artifact"}
    assert executor.calls == 1


@pytest.mark.parametrize(
    ("operation_id", "completed"),
    (("foreign", ("first", "second")), ("batch", ("second",)), ("batch", ("first",))),
)
def test_invalid_receipt_does_not_become_success(
    operation_id: str, completed: tuple[str, ...]
) -> None:
    executor = Executor(
        RunHardwareBatchReceipt(
            operation_id=operation_id, completed_effect_ids=completed
        )
    )
    with pytest.raises(ValueError, match="hardware receipt"):
        execute_domain_batch(
            "execution",
            batch(),
            instruments=executor,
            artifact_fingerprint="artifact",
            result_count=1,
            decode_result=lambda values: len(values),
        )
