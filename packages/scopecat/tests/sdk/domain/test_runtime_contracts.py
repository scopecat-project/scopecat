from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from scopecat.kernel.errors import RunFailed, RunIndeterminate
from scopecat.kernel.problems import (
    Problem,
    ProblemPhase,
    model_location,
    problem,
)
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.sdk.instruments import (
    ApplyReceipt,
    CollectReceipt,
    DriverFault,
    InstrumentReadback,
)
from scopecat.sdk.runtime_problems import problem_from_exception


def _runtime_problem() -> Problem:
    return problem(
        "runtime_test_failed",
        "runtime operation failed",
        phase=ProblemPhase.EXECUTION,
        location=model_location("runtime_contract"),
    )


@pytest.mark.parametrize(
    ("result", "certainty", "problems"),
    [
        ("succeeded", "known", ()),
        ("failed", "known", (_runtime_problem(),)),
        ("failed", "indeterminate", (_runtime_problem(),)),
        ("cancelled", "known", (_runtime_problem(),)),
        ("cancelled", "indeterminate", (_runtime_problem(),)),
    ],
)
def test_run_outcome_accepts_only_coherent_terminal_facts(
    result: str,
    certainty: str,
    problems: tuple[Problem, ...],
) -> None:
    outcome = RunOutcome.model_validate(
        {
            "run_id": "run-contract",
            "result": result,
            "certainty": certainty,
            "problems": problems,
        }
    )

    assert outcome.problems == problems


@pytest.mark.parametrize(
    ("result", "certainty", "problems"),
    [
        ("succeeded", "indeterminate", ()),
        ("succeeded", "known", (_runtime_problem(),)),
        ("failed", "known", ()),
        ("failed", "indeterminate", ()),
        ("cancelled", "known", ()),
        ("cancelled", "indeterminate", ()),
    ],
)
def test_run_outcome_rejects_incoherent_terminal_facts(
    result: str,
    certainty: str,
    problems: tuple[Problem, ...],
) -> None:
    with pytest.raises(ValidationError):
        RunOutcome.model_validate(
            {
                "run_id": "run-contract",
                "result": result,
                "certainty": certainty,
                "problems": problems,
            }
        )


def test_receipts_accept_only_coherent_status_and_problem_facts() -> None:
    selected = _runtime_problem()
    readback = InstrumentReadback()

    assert ApplyReceipt(status="not_applied", problems=(selected,)).status == (
        "not_applied"
    )
    assert ApplyReceipt(status="unknown", problems=(selected,)).status == "unknown"
    assert (
        CollectReceipt(
            status="not_collected",
            problems=(selected,),
        ).status
        == "not_collected"
    )
    assert CollectReceipt(status="unknown", problems=(selected,)).status == "unknown"
    assert CollectReceipt(status="collected", readback=readback).status == "collected"

    with pytest.raises(ValidationError):
        ApplyReceipt(status="applied", problems=(selected,))
    with pytest.raises(ValidationError):
        ApplyReceipt(status="not_applied")
    with pytest.raises(ValidationError):
        ApplyReceipt(status="unknown")
    with pytest.raises(ValidationError):
        CollectReceipt(status="not_collected")
    with pytest.raises(ValidationError):
        CollectReceipt(status="unknown")
    with pytest.raises(ValidationError):
        CollectReceipt(status="collected", problems=(selected,), readback=readback)


def test_driver_fault_carries_one_problem() -> None:
    selected = _runtime_problem()
    error = DriverFault(selected)

    assert error.problem is selected
    assert error.args == (selected.message,)
    assert str(error) == selected.message


def test_expected_driver_fault_is_not_logged_as_an_unexpected_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = _runtime_problem()
    caplog.set_level(logging.ERROR, logger="scopecat.sdk.runtime_problems")

    problem = problem_from_exception(
        "unexpected_driver_failure",
        "driver failed unexpectedly",
        run_id="run-contract",
        operation_id="collect.signal",
        error=DriverFault(source),
    )

    assert problem.code == source.code
    assert caplog.records == []


def test_unexpected_exception_is_logged_but_problem_is_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="scopecat.sdk.runtime_problems")

    problem = problem_from_exception(
        "unexpected_driver_failure",
        "driver failed unexpectedly",
        run_id="run-contract",
        operation_id="collect.signal",
        error=RuntimeError("private device response"),
    )

    assert "private device response" not in problem.message
    assert problem.details["exception_type"] == "builtins.RuntimeError"
    assert [record.getMessage() for record in caplog.records] == [
        "runtime boundary raised an unexpected exception"
    ]


def test_local_assembly_failure_can_expose_its_cause() -> None:
    problem = problem_from_exception(
        "execution_plan_measurement_assembly_failed",
        "execution plan measurement assembly failed",
        run_id="run-contract",
        operation_id="execution-plan.measurements",
        error=TypeError("unsupported persisted scalar: complex"),
        include_exception_message=True,
    )
    assert problem.message.endswith(
        "(TypeError: unsupported persisted scalar: complex)"
    )
    assert problem.details["exception_type"] == "builtins.TypeError"


def test_run_failure_subtypes_require_matching_outcomes() -> None:
    problem = _runtime_problem()
    failed = RunOutcome(
        run_id="run-failed",
        result="failed",
        certainty="known",
        problems=(problem,),
    )
    indeterminate = RunOutcome(
        run_id="run-indeterminate",
        result="failed",
        certainty="indeterminate",
        problems=(problem,),
    )

    assert RunFailed(run_id=failed.run_id, outcome=failed).outcome is failed
    assert (
        RunIndeterminate(
            run_id=indeterminate.run_id,
            outcome=indeterminate,
        ).outcome
        is indeterminate
    )
    with pytest.raises(ValueError, match="known failed"):
        RunFailed(run_id=indeterminate.run_id, outcome=indeterminate)
    with pytest.raises(ValueError, match="indeterminate"):
        RunIndeterminate(run_id=failed.run_id, outcome=failed)
