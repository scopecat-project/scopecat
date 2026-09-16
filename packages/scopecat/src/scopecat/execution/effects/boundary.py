"""Problem and certainty state around one run's external effects."""

from __future__ import annotations

from collections.abc import Mapping

from scopecat.kernel.problems import Problem, ProblemPhase
from scopecat.sdk.runtime_problems import problem_from_exception, runtime_problem


class EffectBoundary:
    """Collect structured failures and external-effect uncertainty for one run."""

    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        self.problems: list[Problem] = []
        self.indeterminate = False
        self.interruption: BaseException | None = None

    def record_interruption(
        self,
        error: BaseException,
        *,
        operation_id: str | None = None,
        point_index: int | None = None,
        instrument_id: str | None = None,
        phase: ProblemPhase = ProblemPhase.EXECUTION,
    ) -> Problem:
        if self.interruption is None:
            self.interruption = error
        problem = self.problem(
            "execution_interrupted",
            f"execution interrupted by {type(error).__name__}",
            operation_id=operation_id,
            point_index=point_index,
            instrument_id=instrument_id,
            phase=phase,
            details={
                "exception_type": f"{type(error).__module__}.{type(error).__qualname__}"
            },
        )
        self.problems.append(problem)
        return problem

    def problem(
        self,
        code: str,
        message: str,
        *,
        operation_id: str | None = None,
        point_index: int | None = None,
        instrument_id: str | None = None,
        phase: ProblemPhase = ProblemPhase.EXECUTION,
        details: Mapping[str, object] | None = None,
    ) -> Problem:
        return runtime_problem(
            code,
            message,
            run_id=self.run_id,
            operation_id=operation_id,
            point_index=point_index,
            instrument_id=instrument_id,
            phase=phase,
            details=details,
        )

    def problem_from_exception(
        self,
        code: str,
        message: str,
        error: Exception,
        *,
        operation_id: str | None = None,
        point_index: int | None = None,
        instrument_id: str | None = None,
        phase: ProblemPhase = ProblemPhase.EXECUTION,
        include_exception_message: bool = False,
    ) -> Problem:
        return problem_from_exception(
            code,
            message,
            run_id=self.run_id,
            error=error,
            operation_id=operation_id,
            point_index=point_index,
            instrument_id=instrument_id,
            phase=phase,
            include_exception_message=include_exception_message,
        )


__all__ = ["EffectBoundary"]
