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
    from scopecat.planning.catalog import InstrumentContractCatalog
    from scopecat.planning.system import ExperimentSystemBuilder
    from scopecat.records.config import ConfigProfileSnapshot


def _system_builder(
    declaration: LabCapabilities, resolve: Callable[[str], object]
) -> ExperimentSystemBuilder | None:
    if declaration.experiment_system:
        return cast("ExperimentSystemBuilder", resolve(declaration.experiment_system))
    if not declaration.domain_systems:
        return None
    from scopecat.planning.system import ExperimentSystem

    builders = {
        kind: cast("ExperimentSystemBuilder", resolve(spec))
        for kind, spec in declaration.domain_systems
    }

    def build(
        config: ConfigProfileSnapshot, catalog: InstrumentContractCatalog
    ) -> ExperimentSystem:
        target = config.domain_target
        if target is None:
            return ExperimentSystem(instrument_catalog=catalog)
        builder = builders.get(target.kind)
        if builder is None:
            raise ValueError(
                f"No installed experiment system capability for target {target.kind!r}"
            )
        return builder(config, catalog)

    return build


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
        build_experiment_system=_system_builder(declaration, resolve),
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
