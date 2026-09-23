"""User-owned execution composition for notebooks and project workers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from scopecat.application.comparison import ComparisonProvider
from scopecat.application.launch import LaunchProvider
from scopecat.automation.definition import ProcedureRegistry
from scopecat.automation.intervals import ProcedureScheduleRegistry

if TYPE_CHECKING:
    from scopecat.api.lab import LabClient
    from scopecat.api.procedure_planner import ProcedurePlanningContext
    from scopecat.application.authoring import AuthorExperiments
    from scopecat.automation.definition import RegisteredProcedure
    from scopecat.automation.intervals import RegisteredProcedureSchedule
    from scopecat.planning.system import ExperimentSystemBuilder


@dataclass(frozen=True, slots=True, init=False)
class LabApplication:
    """Version-controlled executable composition for one lab project.

    The application owns notebook and worker execution capabilities. Daemon
    bootstrap configuration and instrument backend composition are declared
    separately so the server process does not import these execution callbacks.

    """

    launch_provider: LaunchProvider | None = field(default=None, repr=False)
    comparison_provider: ComparisonProvider | None = field(default=None, repr=False)
    authors: AuthorExperiments | None = field(default=None, init=False, repr=False)

    build_experiment_system: ExperimentSystemBuilder | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    procedures: ProcedureRegistry = field(
        default_factory=ProcedureRegistry,
        repr=False,
    )
    procedure_schedules: ProcedureScheduleRegistry[ProcedurePlanningContext] = field(
        default_factory=ProcedureScheduleRegistry,
        repr=False,
    )

    def __init__(
        self,
        build_experiment_system: ExperimentSystemBuilder | None = None,
        procedures: Iterable[RegisteredProcedure] | ProcedureRegistry = (),
        procedure_schedules: (
            Iterable[RegisteredProcedureSchedule[ProcedurePlanningContext]]
            | ProcedureScheduleRegistry[ProcedurePlanningContext]
        ) = (),
        launch_provider: LaunchProvider | None = None,
        author_modules: tuple[str, ...] = (),
        comparison_provider: ComparisonProvider | None = None,
    ) -> None:
        object.__setattr__(
            self,
            "build_experiment_system",
            build_experiment_system,
        )
        object.__setattr__(self, "comparison_provider", comparison_provider)
        authors = None
        if author_modules:
            from scopecat.application.authoring import AuthorExperiments

            authors = AuthorExperiments.discover(*author_modules)
            launch_provider = authors.compose(launch_provider)
            procedures = (
                (*procedures.values(), *authors.procedures)
                if isinstance(procedures, ProcedureRegistry)
                else (*procedures, *authors.procedures)
            )
        elif launch_provider is not None:
            from scopecat.application.authoring import AuthorLaunchProvider

            if isinstance(launch_provider, AuthorLaunchProvider):
                authors = launch_provider.authors
        object.__setattr__(self, "authors", authors)
        object.__setattr__(self, "launch_provider", launch_provider)
        procedure_registry = (
            procedures
            if isinstance(procedures, ProcedureRegistry)
            else ProcedureRegistry(procedures)
        )
        schedule_registry = (
            procedure_schedules
            if isinstance(procedure_schedules, ProcedureScheduleRegistry)
            else ProcedureScheduleRegistry(procedure_schedules)
        )
        for schedule in schedule_registry.values():
            procedure_registry.resolve(schedule.procedure.ref)
        object.__setattr__(self, "procedures", procedure_registry)
        object.__setattr__(self, "procedure_schedules", schedule_registry)

    def connect(
        self,
        daemon: str,
        *,
        operator: str = "operator",
    ) -> LabClient:
        """Connect notebook code while retaining locally authored closures."""

        from scopecat.api.lab import LabClient

        return LabClient(
            daemon,
            build_experiment_system=self.build_experiment_system,
            procedures=self.procedures,
            procedure_schedules=self.procedure_schedules,
            operator=operator,
        )


__all__ = [
    "LabApplication",
]
