"""Public composition of declared experiment and laboratory capabilities."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from scopecat.application.lab import LabApplication

if TYPE_CHECKING:
    from scopecat.api.calibration_planner import CalibrationPlanningContext
    from scopecat.api.calibration_policy import CalibrationPublicationPolicyRegistry
    from scopecat.api.procedure_planner import ProcedurePlanningContext
    from scopecat.application.capabilities import LabCapabilities
    from scopecat.application.comparison import ComparisonProvider
    from scopecat.application.launch import LaunchProvider
    from scopecat.automation.calibration_definition import CalibrationRegistry
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
        calibrations=(
            cast(
                "CalibrationRegistry[CalibrationPlanningContext]",
                resolve(declaration.calibrations),
            )
            if declaration.calibrations
            else ()
        ),
        calibration_publications=(
            cast(
                "CalibrationPublicationPolicyRegistry",
                resolve(declaration.calibration_publications),
            )
            if declaration.calibration_publications
            else ()
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
