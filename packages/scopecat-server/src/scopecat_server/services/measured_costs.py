"""Project one bounded event window without counting receipt mirrors twice."""

from collections.abc import Sequence

from scopecat.control.models import DurableEvent
from scopecat.records.costs import (
    RunCompilationCost,
    RunFinalizationCost,
    RunMeasuredCosts,
    RunOperationCost,
)


def measured_costs(
    events: Sequence[DurableEvent],
    *,
    compilation: RunCompilationCost | None = None,
    truncated: bool = False,
) -> RunMeasuredCosts:
    operations: list[RunOperationCost] = []
    finalizations: list[RunFinalizationCost] = []
    for event in events:
        if event.kind in {
            "run_hardware_batch_measured",
            "run_hardware_batch_failed",
            "run_hardware_batch_unknown",
        }:
            values = event.payload.get("costs")
            if isinstance(values, list):
                operations.extend(
                    RunOperationCost.model_validate(value) for value in values
                )
        elif event.kind == "run_hardware_finalization_measured":
            finalizations.append(
                RunFinalizationCost.model_validate(event.payload["finalization_cost"])
            )
    return RunMeasuredCosts(
        operations=tuple(operations),
        finalizations=tuple(finalizations),
        compilation=compilation,
        truncated=truncated,
    )
