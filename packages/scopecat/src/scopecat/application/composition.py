"""Public composition of declared experiment and laboratory capabilities."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from scopecat.application.lab import LabApplication

if TYPE_CHECKING:
    from scopecat.api.procedure_planner import ProcedurePlanningContext
    from scopecat.application.capabilities import LabCapabilities
    from scopecat.application.comparison import ComparisonProvider
    from scopecat.application.launch import LaunchProvider
    from scopecat.automation.definition import RegisteredProcedure
    from scopecat.automation.intervals import RegisteredProcedureSchedule
    from scopecat.planning.system import ExperimentSystemBuilder


def compose_application(
    declaration: LabCapabilities,
    resolve: Callable[[str], object],
    load_author_module: Callable[[str], object],
) -> LabApplication:
    """Resolve declared values inside the caller's revision/workspace context."""

    for module in declaration.author_modules:
        load_author_module(module)
    return LabApplication(
        author_modules=declaration.author_modules,
        build_experiment_system=(
            cast("ExperimentSystemBuilder", resolve(declaration.experiment_system))
            if declaration.experiment_system
            else None
        ),
        procedures=tuple(
            cast("RegisteredProcedure", resolve(spec))
            for spec in declaration.procedures
        ),
        procedure_schedules=tuple(
            cast("RegisteredProcedureSchedule[ProcedurePlanningContext]", resolve(spec))
            for spec in declaration.procedure_schedules
        ),
        launch_provider=(
            cast("LaunchProvider", resolve(declaration.launch_provider))
            if declaration.launch_provider
            else None
        ),
        comparison_provider=(
            cast("ComparisonProvider", resolve(declaration.comparison_provider))
            if declaration.comparison_provider
            else None
        ),
    )
