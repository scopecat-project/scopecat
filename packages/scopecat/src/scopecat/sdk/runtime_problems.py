"""Runtime problem construction and SDK exception-boundary normalization."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from scopecat.kernel.content_identity import stable_content_hash
from scopecat.kernel.problems import (
    Problem,
    ProblemPhase,
    RuntimeLocation,
)
from scopecat.sdk.instruments.provider import DriverFault

logger = logging.getLogger(__name__)


def runtime_problem(
    code: str,
    message: str,
    *,
    run_id: str,
    operation_id: str | None = None,
    point_index: int | None = None,
    instrument_id: str | None = None,
    phase: ProblemPhase = ProblemPhase.EXECUTION,
    details: Mapping[str, object] | None = None,
    occurrence_index: int = 0,
) -> Problem:
    return Problem(
        code=code,
        phase=phase,
        message=message,
        location=RuntimeLocation(
            run_id=run_id,
            operation_id=operation_id,
            point_index=point_index,
            instrument_id=instrument_id,
        ),
        details={} if details is None else details,
        occurrence_id=_occurrence_id(
            run_id=run_id,
            operation_id=operation_id,
            point_index=point_index,
            instrument_id=instrument_id,
            code=code,
            occurrence_index=occurrence_index,
        ),
    )


def contextualize_problem(
    problem: Problem,
    *,
    run_id: str,
    operation_id: str | None = None,
    point_index: int | None = None,
    instrument_id: str | None = None,
    occurrence_index: int = 0,
) -> Problem:
    if (
        problem.occurrence_id is not None
        and isinstance(problem.location, RuntimeLocation)
        and problem.location.run_id == run_id
    ):
        return problem
    runtime_location = RuntimeLocation(
        run_id=run_id,
        operation_id=operation_id,
        point_index=point_index,
        instrument_id=instrument_id,
    )
    related_locations = problem.related_locations
    if problem.location is not None and problem.location != runtime_location:
        related_locations = (problem.location, *related_locations)
    return problem.model_copy(
        update={
            "location": runtime_location,
            "related_locations": related_locations,
            "occurrence_id": problem.occurrence_id
            or _occurrence_id(
                run_id=run_id,
                operation_id=operation_id,
                point_index=point_index,
                instrument_id=instrument_id,
                code=problem.code,
                occurrence_index=occurrence_index,
            ),
        }
    )


def contextualize_problems(
    problems: Sequence[Problem],
    *,
    run_id: str,
    operation_id: str | None = None,
    point_index: int | None = None,
    instrument_id: str | None = None,
) -> tuple[Problem, ...]:
    return tuple(
        contextualize_problem(
            problem,
            run_id=run_id,
            operation_id=operation_id,
            point_index=point_index,
            instrument_id=instrument_id,
            occurrence_index=index,
        )
        for index, problem in enumerate(problems)
    )


def problem_from_exception(
    code: str,
    message: str,
    *,
    run_id: str,
    error: Exception,
    operation_id: str | None = None,
    point_index: int | None = None,
    instrument_id: str | None = None,
    phase: ProblemPhase = ProblemPhase.EXECUTION,
    include_exception_message: bool = False,
) -> Problem:
    """Log a boundary failure; expose text only for trusted local-code failures."""
    if isinstance(error, DriverFault):
        return contextualize_problem(
            error.problem,
            run_id=run_id,
            operation_id=operation_id,
            point_index=point_index,
            instrument_id=instrument_id,
        )
    logger.error(
        "runtime boundary raised an unexpected exception",
        extra={
            "run_id": run_id,
            "operation_id": operation_id,
            "instrument_id": instrument_id,
            "problem_code": code,
        },
        exc_info=(type(error), error, error.__traceback__),
    )
    exception_type = type(error).__name__
    summary = (
        f"{exception_type}: {error}" if include_exception_message else exception_type
    )
    return runtime_problem(
        code,
        f"{message} ({summary})",
        run_id=run_id,
        operation_id=operation_id,
        point_index=point_index,
        instrument_id=instrument_id,
        phase=phase,
        details={
            "exception_type": f"{type(error).__module__}.{type(error).__qualname__}"
        },
    )


def _occurrence_id(
    *,
    run_id: str,
    operation_id: str | None,
    point_index: int | None,
    instrument_id: str | None,
    code: str,
    occurrence_index: int,
) -> str:
    digest = stable_content_hash(
        {
            "run_id": run_id,
            "operation_id": operation_id,
            "point_index": point_index,
            "instrument_id": instrument_id,
            "code": code,
            "occurrence_index": occurrence_index,
        }
    )
    return f"problem-{digest[:24]}"
